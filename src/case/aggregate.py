"""Case asset and load aggregation."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr


def aggregate_assets(
    data: gpd.GeoDataFrame,
    parameter: pd.DataFrame,
    *,
    dataset_id: str,
    method: str,
    buses: gpd.GeoDataFrame,
    cells: gpd.GeoDataFrame,
    sum_names: list[str],
    boolean_names: list[str],
) -> tuple[gpd.GeoDataFrame, pd.DataFrame, pd.DataFrame]:
    """Aggregate assets by bus/cell, class, and subclass."""

    if method == "none":
        membership = pd.DataFrame({
            "source_uid": data["uid"], "aggregate_uid": data["uid"],
            "weight": 1.0,
        })
        return data.copy(), parameter.copy(), membership
    if method not in {"bus", "cell"}:
        raise ValueError("Asset aggregation must be 'none', 'bus', or 'cell'.")

    key = "bus_uid" if method == "bus" else "spatial_uid"
    capacity = "capacity_mw" if dataset_id == "generator" else "power_capacity_mw"
    source = data.copy()
    source["_group"] = (
        source[key].astype(str) + ":" + source["class"].astype(str)
        + ":" + source["subclass"].fillna("unspecified").astype(str)
    )
    source["_aggregate_uid"] = (
        "case:" + dataset_id + ":" + source["_group"]
    )
    totals = source.groupby("_aggregate_uid")[capacity].transform("sum")
    membership = pd.DataFrame({
        "source_uid": source["uid"].to_numpy(),
        "aggregate_uid": source["_aggregate_uid"].to_numpy(),
        "weight": np.divide(
            source[capacity].to_numpy(float), totals.to_numpy(float),
            out=np.zeros(len(source)), where=totals.to_numpy(float) != 0,
        ),
        "source_capacity_mw": source[capacity].to_numpy(float),
        "source_spatial_uid": source["spatial_uid"].to_numpy(),
        "source_bus_uid": source["bus_uid"].to_numpy(),
    })

    rows = []
    bus_geometry = buses.set_index("uid")["geometry"]
    cell_geometry = cells.set_index("spatial_uid")["centre_geometry"]
    for aggregate_uid, group in source.groupby("_aggregate_uid", sort=True):
        first = group.iloc[0].copy()
        first["uid"] = aggregate_uid
        first[capacity] = group[capacity].sum()
        if dataset_id == "storage":
            first["energy_capacity_mwh"] = group["energy_capacity_mwh"].sum(min_count=1)
            first["duration_h"] = first["energy_capacity_mwh"] / first[capacity]
        first["status"] = "aggregated"
        first["source_uid"] = ";".join(sorted(group["uid"].astype(str)))
        first["geometry"] = (
            bus_geometry.get(first["bus_uid"])
            if method == "bus" else cell_geometry.get(first["spatial_uid"])
        )
        first["geometry_method"] = f"case_{method}_aggregation"
        rows.append(first.drop(labels=["_group", "_aggregate_uid"]))
    result = gpd.GeoDataFrame(rows, geometry="geometry", crs=data.crs).reset_index(drop=True)
    aggregated_parameter = _aggregate_parameters(
        parameter, membership, sum_names=sum_names, boolean_names=boolean_names
    )
    return result, aggregated_parameter, membership


def aggregate_load(data: xr.Dataset, method: str) -> xr.Dataset:
    """Aggregate load locations by retained bus or spatial cell."""

    if method == "none":
        return data.copy()
    if method not in {"bus", "cell"}:
        raise ValueError("Load aggregation must be 'none', 'bus', or 'cell'.")
    coordinate = "bus_uid" if method == "bus" else "spatial_uid"
    groups = data[coordinate].values.astype(str)
    unique, inverse = np.unique(groups, return_inverse=True)
    values = np.asarray(data["demand_mw"].values)
    output = np.zeros((values.shape[0], len(unique), values.shape[2]), dtype=float)
    for source_index, target_index in enumerate(inverse):
        output[:, target_index, :] += values[:, source_index, :]
    result = xr.Dataset(
        {"demand_mw": (("time", "uid", "class"), output)},
        coords={
            "time": data.time.values,
            "uid": unique,
            "class": data["class"].values,
            coordinate: ("uid", unique),
        },
        attrs={**data.attrs, "case_aggregation": method},
    )
    if method == "bus":
        result = result.assign_coords(bus_uid=("uid", unique))
    return result


def _aggregate_parameters(
    parameter: pd.DataFrame,
    membership: pd.DataFrame,
    *,
    sum_names: list[str],
    boolean_names: list[str],
) -> pd.DataFrame:
    resolved = parameter.loc[parameter["value"].notna()].merge(
        membership, left_on="uid", right_on="source_uid", how="inner"
    )
    if resolved.empty:
        return parameter.iloc[0:0].copy()
    rows = []
    for (uid, name, unit), group in resolved.groupby(
        ["aggregate_uid", "name", "unit"], dropna=False, sort=True
    ):
        first = group.iloc[0].copy()
        first["uid"] = uid
        if name in sum_names:
            value = group["value"].astype(float).sum()
        elif name in boolean_names:
            value = float(group["value"].astype(float).max())
        else:
            weights = group["source_capacity_mw"].astype(float)
            value = np.average(group["value"].astype(float), weights=weights)
        first["value"] = value
        first["match_rank"] = pd.NA
        first["match_result"] = "aggregated"
        first["match_info"] = (
            f"aggregated_parameter_records={len(group)}"
        )
        first["selected_parameter_uid"] = ";".join(
            sorted(group["selected_parameter_uid"].dropna().astype(str).unique())
        )
        first["source_id"] = ";".join(
            sorted(group["source_id"].dropna().astype(str).unique())
        )
        first["source_uid"] = ";".join(
            sorted(group["source_uid_x"].dropna().astype(str).unique())
        )
        rows.append(first[parameter.columns])
    return pd.DataFrame(rows).reset_index(drop=True)
