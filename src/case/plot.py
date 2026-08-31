"""Case maps using the shared standard/mapping visual language."""

from __future__ import annotations

from collections.abc import Iterable

import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
import numpy as np
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
    capacity_legend_values,
    draw_background,
    draw_boundaries,
    filter_spatial_levels,
    finish_map,
    map_axes,
)
from ..visualization.labels import class_label, text
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
            return plot_case_load(case, **common, **kwargs)
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


def plot_case_load(
    case: PowerSystemCase,
    *,
    year: int | None = None,
    class_name: str | None = None,
    statistic: str = "peak",
    spatial: gpd.GeoDataFrame | None = None,
    map_crs: str = DEFAULT_MAP_CRS,
    china_inset: bool | None = None,
    language: str = "zh",
    **_: object,
) -> PlotResult:
    """Plot bus-level case load with circle area proportional to MW."""

    prepared = _prepare_case_load_points(
        case, year=year, class_name=class_name, statistic=statistic,
        language=language,
    )
    figures = {
        item: _load_bubble_map(
            frame, metadata=metadata, spatial=spatial, map_crs=map_crs,
            china_inset=china_inset,
        )
        for item, (frame, metadata) in prepared.items()
    }
    return figures[str(class_name)] if class_name else figures


def _prepare_case_load_points(
    case: PowerSystemCase,
    *,
    year: int | None,
    class_name: str | None,
    statistic: str,
    language: str,
) -> dict[str, tuple[gpd.GeoDataFrame, dict[str, object]]]:
    if statistic not in {"peak", "mean"}:
        raise ValueError("Case load statistic must be 'peak' or 'mean'.")
    data = case.load
    available = data["class"].values.astype(str).tolist()
    classes = [str(class_name)] if class_name else available
    unknown = set(classes).difference(available)
    if unknown:
        raise KeyError(f"Unknown load class values: {sorted(unknown)}")
    buses = case.network.bus.data.set_index("uid")
    uids = data["uid"].values.astype(str)
    geometry = buses.geometry.reindex(uids)
    if geometry.isna().any():
        missing = geometry.index[geometry.isna()].tolist()[:5]
        raise ValueError(f"Case load references buses without geometry: {missing}")
    # OSM-derived bus geometries can be lines or polygons, not just points.
    geometry = geometry.representative_point()
    selected = data.sel(time=str(year)) if year is not None else data
    if selected.sizes.get("time", 0) == 0:
        raise ValueError(f"No case load data are available for year={year}.")
    start = pd.Timestamp(selected.time.values[0]).strftime("%Y-%m-%d")
    end = pd.Timestamp(selected.time.values[-1]).strftime("%Y-%m-%d")
    prepared = {}
    for item in classes:
        values = selected["demand_mw"].sel(**{"class": item})
        reduced = (
            values.max("time", skipna=True)
            if statistic == "peak" else values.mean("time", skipna=True)
        )
        frame = gpd.GeoDataFrame(
            {"bus_uid": uids, "_value": reduced.compute().values},
            geometry=geometry.to_numpy(), crs=case.network.bus.data.crs,
        )
        prepared[item] = (frame, {
            "title": text(
                f"case_load_{statistic}_title", language,
                class_label=class_label(item, language), start=start, end=end,
            ),
            "legend_title": text(f"{statistic}_load", language),
            "unit": "MW",
            "statistic": statistic,
        })
    return prepared


def _load_bubble_map(
    frame: gpd.GeoDataFrame,
    *,
    metadata: dict[str, object],
    spatial: gpd.GeoDataFrame | None,
    map_crs: str,
    china_inset: bool | None,
) -> Figure:
    frame = frame.loc[
        frame.geometry.notna() & pd.to_numeric(frame["_value"], errors="coerce").gt(0)
    ].copy().to_crs(map_crs)
    reference = (
        max(float(frame["_value"].quantile(0.99)), 1.0)
        if not frame.empty else 1.0
    )
    frame["_marker_size"] = 420.0 * (
        frame["_value"] / reference
    ).clip(0.015, 1)
    figure, axes = map_axes(spatial, china_inset=china_inset)
    for index, axis in enumerate(axes):
        draw_background(axis, spatial, map_crs=map_crs, zorder=0)
        if not frame.empty:
            frame.sort_values("_value").plot(
                ax=axis, marker="o", markersize="_marker_size",
                color="#d1495b", edgecolor="white", linewidth=0.18,
                alpha=0.64, zorder=3,
            )
        draw_boundaries(axis, spatial, map_crs=map_crs, zorder=4)
        if index == 0:
            values = capacity_legend_values(reference)
            axis.legend(
                handles=[
                    Line2D(
                        [], [], marker="o", linestyle="none",
                        markerfacecolor="#d1495b", markeredgecolor="white",
                        markersize=np.sqrt(
                            420.0 * max(min(value / reference, 1.0), 0.015)
                        ),
                        label=f"{value:,.0f} MW",
                    )
                    for value in values
                ],
                title=str(metadata["legend_title"]), loc="lower left",
                bbox_to_anchor=(0.095, 0.035), frameon=False,
                fontsize=7, title_fontsize=8,
            )
        finish_map(axis, str(metadata["title"]) if index == 0 else "")
    return figure


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
