"""Case maps using the shared standard/mapping visual language."""

from __future__ import annotations

from collections.abc import Iterable

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
import xarray as xr

from ..mapping.model import MappedNetwork
from ..mapping.plot import (
    asset_pie_map,
    plot_network as plot_mapped_network,
    plot_population as plot_mapped_population,
    plot_resource as plot_mapped_resource,
    plot_spatial as plot_mapped_spatial,
)
from ..standard.plot import (
    DEFAULT_MAP_CRS,
    PlotResult,
    continuous_map,
    filter_spatial_levels,
)
from .model import PowerSystemCase


CASE_COMPONENTS = (
    "spatial", "population", "load", "resource", "network", "generator",
    "storage",
)


def plot_case(
    case: PowerSystemCase,
    component: str,
    *,
    spatial: gpd.GeoDataFrame | None = None,
    spatial_levels: str | Iterable[str] | None = None,
    map_crs: str = DEFAULT_MAP_CRS,
    china_inset: bool | None = None,
    **kwargs: object,
) -> PlotResult:
    """Plot one case component with the common map API."""

    if component not in CASE_COMPONENTS:
        raise KeyError(
            f"Unknown case component {component!r}; expected {CASE_COMPONENTS}."
        )
    background = spatial if spatial is not None else _background(case.spatial)
    if spatial_levels is not None:
        background = filter_spatial_levels(background, spatial_levels)
    cells = case.spatial
    if spatial_levels is not None:
        selected = set(background["level"].astype(str))
        cells = cells.loc[cells["spatial_level"].astype(str).isin(selected)]
    common = {
        "spatial": background,
        "map_crs": map_crs,
        "china_inset": china_inset,
    }
    with plt.ioff():
        if component == "spatial":
            return plot_mapped_spatial(cells, **common, **kwargs)
        if component == "population":
            return plot_mapped_population(case.population, **common, **kwargs)
        if component == "resource":
            return plot_mapped_resource(case.resource, **common, **kwargs)
        if component == "network":
            network = MappedNetwork(
                case.network.bus.data,
                case.network.branch.data,
                case.network.transformer.data,
                case.network.converter.data,
                case.network.branch_mapping,
            )
            kwargs.setdefault(
                "title",
                f"Case network: {len(network.bus):,} buses, "
                f"{len(network.branch):,} branches",
            )
            return plot_mapped_network(
                network,
                cells=cells,
                **common,
                **kwargs,
            )
        if component in {"generator", "storage"}:
            data = getattr(case, component).data.copy()
            data["spatial_uid"] = data["bus_uid"].astype(str)
            bus_cells = case.network.bus.data[["uid", "geometry"]].copy()
            bus_cells = bus_cells.rename(columns={"uid": "spatial_uid"})
            return asset_pie_map(
                data,
                bus_cells,
                "capacity_mw" if component == "generator" else "power_capacity_mw",
                str(kwargs.get("title", f"Case {component}")),
                background,
                map_crs,
                china_inset,
            )
        return _plot_load(
            case,
            spatial=background,
            map_crs=map_crs,
            china_inset=china_inset,
            **kwargs,
        )


def _plot_load(
    case: PowerSystemCase,
    *,
    spatial: gpd.GeoDataFrame,
    map_crs: str,
    china_inset: bool | None,
    start: object = None,
    end: object = None,
    class_name: str | None = None,
    title: str = "Case mean nodal load",
    **_: object,
):
    data = case.load["demand_mw"].sel(time=slice(start, end))
    if class_name is not None:
        data = data.sel(**{"class": class_name})
    else:
        data = data.sum("class")
    values = data.mean("time").compute().to_series()
    buses = case.network.bus.data.loc[
        case.network.bus.data["uid"].astype(str).isin(values.index.astype(str))
    ].copy()
    buses["_value"] = buses["uid"].astype(str).map(values)
    return continuous_map(
        buses,
        "_value",
        spatial=spatial,
        title=title,
        label="Mean load (MW)",
        map_crs=map_crs,
        china_inset=china_inset,
    )


def _background(cells: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Recover an administrative display boundary from case cells."""

    frame = cells[["admin_uid", "spatial_level", "geometry"]].dissolve(
        by=["admin_uid", "spatial_level"], as_index=False
    )
    return frame.rename(columns={
        "admin_uid": "uid", "spatial_level": "level",
    })
