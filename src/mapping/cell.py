"""Standard spatial-cell construction."""

from __future__ import annotations

from pathlib import Path
import math
import warnings

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box

from ..standard import polygonal_geometry


def build_spatial_cells(
    spatial: gpd.GeoDataFrame,
    options: dict[str, object],
    *,
    metric_crs: str,
    project_root: Path,
) -> gpd.GeoDataFrame:
    """Clip regular or user-supplied cells by configured spatial units."""

    level_priority = [
        str(level) for level in options.get("level_priority", []) if level
    ]
    if not level_priority or len(level_priority) != len(set(level_priority)):
        raise ValueError("cell.level_priority must contain unique spatial levels.")
    spatial_units = spatial.loc[
        spatial["level"].isin(level_priority), ["uid", "level", "geometry"]
    ].copy()
    if spatial_units.empty:
        available = sorted(spatial["level"].dropna().unique())
        raise ValueError(
            f"spatial has none of levels={level_priority!r}; available levels are "
            f"{available}."
        )
    missing = set(level_priority).difference(spatial_units["level"].unique())
    if missing:
        raise ValueError(f"Configured spatial levels are unavailable: {sorted(missing)}")
    spatial_units = spatial_units.rename(columns={
        "uid": "admin_uid",
        "level": "spatial_level",
    }).to_crs(metric_crs)
    spatial_units["geometry"] = spatial_units.geometry.map(polygonal_geometry)
    spatial_units = _apply_level_priority(spatial_units, level_priority)
    source_cells = _source_cells(
        spatial_units, options, project_root, metric_crs
    )
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="invalid value encountered in intersection",
            category=RuntimeWarning,
        )
        cells = gpd.overlay(
            source_cells,
            spatial_units,
            how="intersection",
            keep_geom_type=True,
        ).explode(index_parts=False, ignore_index=True)
    cells = cells.loc[~cells.geometry.is_empty].copy()
    cells = cells.dissolve(
        by=["admin_uid", "spatial_level", "source_cell_uid"],
        as_index=False,
    )
    cells["area_km2"] = cells.geometry.area / 1e6
    cells = _merge_small_cells(
        cells,
        minimum_area_km2=float(options["minimum_area_km2"]),
    )
    cells["centre_geometry"] = cells.geometry.centroid
    cells["spatial_uid"] = [
        f"cell:{row.admin_uid}:{row.source_cell_uid}"
        for row in cells.itertuples()
    ]
    cells["cell_kind"] = str(options["kind"])
    columns = [
        "spatial_uid", "admin_uid", "geometry", "centre_geometry",
        "area_km2", "spatial_level", "cell_kind", "source_cell_uid",
    ]
    centres = gpd.GeoSeries(
        cells["centre_geometry"],
        crs=metric_crs,
    ).to_crs(spatial.crs)
    result = cells.loc[:, columns].drop(columns="centre_geometry").to_crs(
        spatial.crs
    )
    result["centre_geometry"] = centres.reset_index(drop=True)
    result = result.loc[:, columns]
    result.attrs.update({
        "mapping_dataset_id": "spatial",
    })
    if result["spatial_uid"].duplicated().any():
        raise ValueError("Generated spatial_uid values are not unique.")
    return result


def _apply_level_priority(
    spatial_units: gpd.GeoDataFrame,
    level_priority: list[str],
) -> gpd.GeoDataFrame:
    """Make different spatial levels disjoint using configured precedence."""

    resolved = []
    occupied_geometry = None
    for level in level_priority:
        current = spatial_units.loc[
            spatial_units["spatial_level"].eq(level)
        ].copy()
        if occupied_geometry is not None:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message="invalid value encountered in difference",
                    category=RuntimeWarning,
                )
                current["geometry"] = current.geometry.map(
                    lambda geometry: polygonal_geometry(
                        geometry.difference(occupied_geometry)
                    )
                )
        current = current.loc[~current.geometry.is_empty].copy()
        if current.empty:
            continue
        resolved.append(current)
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="invalid value encountered in unary_union",
                category=RuntimeWarning,
            )
            occupied_geometry = polygonal_geometry(
                gpd.GeoSeries(
                    [
                        *([] if occupied_geometry is None else [occupied_geometry]),
                        *current.geometry,
                    ],
                    crs=spatial_units.crs,
                ).union_all()
            )
    return gpd.GeoDataFrame(
        pd.concat(resolved, ignore_index=True),
        geometry="geometry",
        crs=spatial_units.crs,
    )


def _source_cells(
    spatial_units: gpd.GeoDataFrame,
    options: dict[str, object],
    project_root: Path,
    metric_crs: str,
) -> gpd.GeoDataFrame:
    kind = str(options["kind"])
    if kind == "square":
        size_m = float(options["size_km"]) * 1000
        if size_m <= 0:
            raise ValueError("cell.size_km must be positive.")
        minx, miny, maxx, maxy = spatial_units.total_bounds
        x0, y0 = math.floor(minx / size_m) * size_m, math.floor(miny / size_m) * size_m
        xs = np.arange(x0, maxx, size_m)
        ys = np.arange(y0, maxy, size_m)
        records = [
            {
                "source_cell_uid": f"square:{size_m:g}:{x_index}:{y_index}",
                "geometry": box(x, y, x + size_m, y + size_m),
            }
            for y_index, y in enumerate(ys)
            for x_index, x in enumerate(xs)
        ]
        return gpd.GeoDataFrame(records, geometry="geometry", crs=metric_crs)
    if kind == "polygon":
        path = project_root / str(options.get("polygon_file", ""))
        if not path.is_file():
            raise FileNotFoundError(f"Configured polygon cells do not exist: {path}")
        frame = gpd.read_file(path).to_crs(metric_crs).explode(
            index_parts=False,
            ignore_index=True,
        )
        if not frame.geom_type.isin(["Polygon", "MultiPolygon"]).all():
            raise TypeError("Custom cells must contain only polygon geometries.")
        frame["source_cell_uid"] = [f"polygon:{index}" for index in frame.index]
        return frame[["source_cell_uid", "geometry"]]
    raise ValueError("cell.kind must be 'square' or 'polygon'.")


def _merge_small_cells(
    cells: gpd.GeoDataFrame,
    *,
    minimum_area_km2: float,
) -> gpd.GeoDataFrame:
    if minimum_area_km2 < 0:
        raise ValueError("cell.minimum_area_km2 cannot be negative.")
    merged = []
    for _, group in cells.groupby("admin_uid", sort=True):
        group = group.reset_index(drop=True)
        large = group.loc[group["area_km2"].ge(minimum_area_km2)].copy()
        small = group.loc[group["area_km2"].lt(minimum_area_km2)]
        if large.empty:
            row = group.iloc[0].copy()
            row.geometry = group.geometry.union_all()
            row["area_km2"] = row.geometry.area / 1e6
            merged.append(gpd.GeoDataFrame([row], crs=cells.crs))
            continue
        for row in small.itertuples():
            nearest = large.geometry.centroid.distance(row.geometry.centroid).idxmin()
            large.at[nearest, "geometry"] = large.at[nearest, "geometry"].union(
                row.geometry
            )
        large["area_km2"] = large.geometry.area / 1e6
        merged.append(large)
    return gpd.GeoDataFrame(
        pd.concat(merged, ignore_index=True),
        geometry="geometry",
        crs=cells.crs,
    )
