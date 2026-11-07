"""Network display selection independent of data-layer objects."""

from __future__ import annotations

import geopandas as gpd


def filter_network_voltage(
    buses: gpd.GeoDataFrame,
    branches: gpd.GeoDataFrame,
    *,
    minimum_voltage_kv: float | None = None,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Select display tables without changing inputs or network connectivity."""

    if minimum_voltage_kv is None:
        return buses.copy(), branches.copy()
    buses = buses.loc[buses["voltage_kv"].ge(minimum_voltage_kv)].copy()
    branches = branches.loc[
        branches["voltage_kv"].ge(minimum_voltage_kv)
        & branches["from_bus_uid"].isin(buses["uid"])
        & branches["to_bus_uid"].isin(buses["uid"])
    ].copy()
    return buses, branches
