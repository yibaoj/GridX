"""Conservative spatial mapping to standard cells."""

from __future__ import annotations

import warnings

import dask.array as da
import geopandas as gpd
import numpy as np
import pandas as pd
from scipy import sparse
from shapely.geometry import box
import xarray as xr

from ..standard import polygonal_geometry


def aggregate_extensive(
    data: gpd.GeoDataFrame,
    cells: gpd.GeoDataFrame,
    *,
    value_column: str,
    metric_crs: str,
    nominal_cell_area_km2: float,
    require_finer: bool,
) -> gpd.GeoDataFrame:
    """Conservatively allocate an extensive auxiliary field to cells."""

    if value_column not in data:
        raise KeyError(f"Auxiliary data has no {value_column!r} column.")
    source = data.to_crs(metric_crs).copy()
    source_area = source.geometry.area / 1e6
    if require_finer and source_area.max() > nominal_cell_area_km2 * 1.01:
        raise ValueError(
            "Auxiliary source cells are coarser than the configured standard cells."
        )
    crosswalk = _area_crosswalk(source, cells, metric_crs=metric_crs)
    values = data.set_index("uid")[value_column].astype(float)
    crosswalk[value_column] = (
        crosswalk["source_uid"].map(values)
        * crosswalk["overlap_area_m2"]
        / crosswalk["source_area_m2"]
    )
    totals = crosswalk.groupby("spatial_uid")[value_column].sum()
    result = cells.copy()
    result[value_column] = result["spatial_uid"].map(totals).fillna(0.0)
    result.attrs.update({
        "mapping_dataset_id": str(data.attrs["standard_dataset_id"]),
    })
    return result


def map_timeseries_to_cells(
    data: xr.Dataset,
    cells: gpd.GeoDataFrame,
    *,
    variable: str,
    quantity_kind: str,
    method: str,
    metric_crs: str,
    auxiliary_cells: gpd.GeoDataFrame | None = None,
    auxiliary_value: str | None = None,
    source_cell_width_degrees: float | None = None,
    source_cell_height_degrees: float | None = None,
    conservation_tolerance: float = 0.005,
) -> xr.Dataset:
    """Map a time-by-location array to standard cells with sparse weights."""

    source = _xarray_support(
        data,
        width_degrees=source_cell_width_degrees,
        height_degrees=source_cell_height_degrees,
    )
    crosswalk = _area_crosswalk(source, cells, metric_crs=metric_crs)
    target_uids = cells["spatial_uid"].astype(str).to_numpy()
    weights = _weights(
        crosswalk,
        source_uids=data["uid"].values.astype(str),
        target_uids=target_uids,
        quantity_kind=quantity_kind,
        method=method,
        auxiliary_cells=auxiliary_cells,
        auxiliary_value=auxiliary_value,
        tolerance=conservation_tolerance,
    )
    values = data[variable].transpose("time", "uid", "class").data
    values = da.asarray(values).rechunk({1: -1})
    mapped = da.map_blocks(
        _sparse_dot,
        values,
        weights=weights,
        dtype=np.float32,
        chunks=(values.chunks[0], (len(cells),), values.chunks[2]),
    )
    result = xr.Dataset(
        {variable: (("time", "uid", "class"), mapped)},
        coords={
            "time": data["time"].values,
            "uid": cells["spatial_uid"].astype(str).to_numpy(),
            "spatial_uid": (
                "uid", cells["spatial_uid"].astype(str).to_numpy()
            ),
            "spatial_level": (
                "uid", cells["spatial_level"].astype(str).to_numpy()
            ),
            "class": data["class"].values,
            "location": ("uid", cells["admin_uid"].astype(str).to_numpy()),
            "geometry": ("uid", cells.geometry.to_wkt().to_numpy()),
            "geometry_method": ("uid", ["standard_cell"] * len(cells)),
        },
        attrs={
            **data.attrs,
            "mapping_dataset_id": str(data.attrs["standard_dataset_id"]),
            "crs": cells.crs.to_string(),
            "spatial_method": method,
        },
    )
    covered = xr.DataArray(
        np.isin(target_uids, crosswalk["spatial_uid"].unique()),
        dims="uid",
        coords={"uid": result["uid"]},
    )
    result[variable] = (
        result[variable].where(covered, 0.0)
        if quantity_kind == "extensive"
        else result[variable].where(covered)
    )
    return result


def _xarray_support(
    data: xr.Dataset,
    *,
    width_degrees: float | None,
    height_degrees: float | None,
) -> gpd.GeoDataFrame:
    geometry = gpd.GeoSeries.from_wkt(
        data["geometry"].values,
        crs=str(data.attrs["crs"]),
    )
    if geometry.geom_type.eq("Point").all():
        if not width_degrees or not height_degrees:
            raise ValueError(
                "Point-based source cells require configured width and height."
            )
        geometry = gpd.GeoSeries(
            [
                box(
                    point.x - width_degrees / 2,
                    point.y - height_degrees / 2,
                    point.x + width_degrees / 2,
                    point.y + height_degrees / 2,
                )
                for point in geometry
            ],
            crs=geometry.crs,
        )
    else:
        geometry = gpd.GeoSeries(
            geometry.map(polygonal_geometry),
            crs=geometry.crs,
        )
    if not geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise TypeError("Spatial mapping requires polygon source support.")
    return gpd.GeoDataFrame(
        {"uid": data["uid"].values.astype(str)},
        geometry=geometry,
        crs=geometry.crs,
    )


def _area_crosswalk(
    source: gpd.GeoDataFrame,
    cells: gpd.GeoDataFrame,
    *,
    metric_crs: str,
) -> pd.DataFrame:
    source = source[["uid", "geometry"]].rename(columns={"uid": "source_uid"})
    source = source.to_crs(metric_crs)
    source["source_area_m2"] = source.geometry.area
    target = cells[["spatial_uid", "geometry"]].to_crs(metric_crs)
    target["target_area_m2"] = target.geometry.area
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="invalid value encountered in intersection",
            category=RuntimeWarning,
        )
        overlap = gpd.overlay(
            source,
            target,
            how="intersection",
            keep_geom_type=True,
        )
    overlap["overlap_area_m2"] = overlap.geometry.area
    return pd.DataFrame(overlap.drop(columns="geometry"))


def _weights(
    crosswalk: pd.DataFrame,
    *,
    source_uids: np.ndarray,
    target_uids: np.ndarray,
    quantity_kind: str,
    method: str,
    auxiliary_cells: gpd.GeoDataFrame | None,
    auxiliary_value: str | None,
    tolerance: float,
) -> sparse.csr_matrix:
    source_index = pd.Series(np.arange(len(source_uids)), index=source_uids)
    target_index = pd.Series(np.arange(len(target_uids)), index=target_uids)
    rows = crosswalk["source_uid"].map(source_index)
    columns = crosswalk["spatial_uid"].map(target_index)
    if rows.isna().any() or columns.isna().any():
        raise ValueError("Spatial crosswalk contains unknown source or target UIDs.")

    if quantity_kind == "extensive" and method != "linear":
        if auxiliary_cells is None or auxiliary_value is None:
            raise ValueError("Auxiliary downscaling requires mapped auxiliary data.")
        auxiliary = auxiliary_cells.set_index("spatial_uid")[auxiliary_value]
        raw = (
            crosswalk["spatial_uid"].map(auxiliary).fillna(0).to_numpy()
            * crosswalk["overlap_area_m2"].to_numpy()
            / crosswalk["target_area_m2"].to_numpy()
        )
        totals = pd.Series(raw).groupby(crosswalk["source_uid"]).transform("sum")
        fallback = crosswalk["overlap_area_m2"] / crosswalk.groupby(
            "source_uid"
        )["overlap_area_m2"].transform("sum")
        values = np.where(
            totals.gt(0),
            raw / totals.where(totals.gt(0), 1),
            fallback,
        )
    elif quantity_kind == "extensive" and method == "linear":
        values = crosswalk["overlap_area_m2"] / crosswalk["source_area_m2"]
    elif quantity_kind == "intensive" and method == "linear":
        values = crosswalk["overlap_area_m2"] / crosswalk.groupby(
            "spatial_uid"
        )["overlap_area_m2"].transform("sum")
    else:
        raise ValueError(
            "Supported combinations are extensive/linear, "
            "extensive/auxiliary-dataset, and intensive/linear."
        )
    matrix = sparse.coo_matrix(
        (np.asarray(values, dtype="float64"), (rows.astype(int), columns.astype(int))),
        shape=(len(source_uids), len(target_uids)),
    ).tocsr()
    if quantity_kind == "extensive":
        covered = np.asarray(matrix.sum(axis=1)).ravel()
        if not np.allclose(covered, 1, atol=tolerance, rtol=0):
            worst = float(np.max(np.abs(covered - 1)))
            raise ValueError(
                f"Extensive mapping is not conservative; maximum error={worst:.4g}."
            )
    return matrix


def _sparse_dot(block: np.ndarray, *, weights: sparse.csr_matrix) -> np.ndarray:
    time_count, source_count, class_count = block.shape
    flat = block.transpose(0, 2, 1).reshape(-1, source_count)
    mapped = weights.T.dot(flat.T).T
    return np.asarray(mapped, dtype="float32").reshape(
        time_count,
        class_count,
        weights.shape[1],
    ).transpose(0, 2, 1)
