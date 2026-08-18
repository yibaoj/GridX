"""Case maps using the shared standard/mapping visual language."""

from __future__ import annotations

from collections.abc import Iterable

import geopandas as gpd
import matplotlib.pyplot as plt
import xarray as xr

from ..mapping.model import MappedNetwork
from ..mapping.plot import (
    asset_pie_map,
    plot_load as plot_mapped_load,
    plot_network as plot_mapped_network,
    plot_population as plot_mapped_population,
    plot_resource as plot_mapped_resource,
    plot_spatial as plot_mapped_spatial,
)
from ..standard.plot import (
    DEFAULT_MAP_CRS,
    PlotResult,
    filter_spatial_levels,
)
from ..visualization.labels import text
from ..visualization.spatial import spatial_background
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
    language: str = "zh",
    **kwargs: object,
) -> PlotResult:
    """Plot one case component with the common map API."""

    if component not in CASE_COMPONENTS:
        raise KeyError(
            f"Unknown case component {component!r}; expected {CASE_COMPONENTS}."
        )
    background = spatial if spatial is not None else spatial_background(case.spatial)
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
        "language": language,
    }
    with plt.ioff():
        if component == "spatial":
            return plot_mapped_spatial(cells, **common, **kwargs)
        if component == "population":
            return plot_mapped_population(case.population, **common, **kwargs)
        if component == "resource":
            return plot_mapped_resource(case.resource, **common, **kwargs)
        if component == "load":
            return plot_mapped_load(
                load_with_bus_geometry(case), **common, **kwargs
            )
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
                text(
                    "case_network", language,
                    buses=len(network.bus), branches=len(network.branch),
                ),
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
                str(kwargs.get(
                    "title", text(f"case_{component}", language)
                )),
                background,
                map_crs,
                china_inset,
                language,
            )


def load_with_bus_geometry(case: PowerSystemCase) -> xr.Dataset:
    """Attach case-bus geometry to nodal load for shared map preparation."""

    data = case.load
    buses = case.network.bus.data.set_index("uid")
    uids = data["uid"].values.astype(str)
    geometry = buses.geometry.reindex(uids)
    if geometry.isna().any():
        missing = geometry.index[geometry.isna()].tolist()[:5]
        raise ValueError(f"Case load references buses without geometry: {missing}")
    result = data.assign_coords(
        geometry=("uid", geometry.to_wkt().to_numpy()),
        geometry_method=("uid", ["case_bus_geometry"] * len(uids)),
        location=("uid", uids),
    )
    result.attrs["crs"] = str(case.network.bus.data.crs)
    return result
