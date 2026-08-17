"""Plots for mapped datasets using the standard-layer map style."""

from __future__ import annotations

from collections.abc import Callable

import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.collections import PatchCollection
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Wedge
import numpy as np
import pandas as pd
import xarray as xr

from ..standard.plot import (
    BOUNDARY_COLOR,
    CATEGORY_COLORS,
    CELL_COLOR,
    DEFAULT_MAP_CRS,
    PlotResult,
    add_asset_legends,
    class_label,
    continuous_map,
    draw_background,
    draw_boundaries,
    finish_map,
    map_axes,
    prepare_network_plot,
    prepare_population_plot,
    timeseries_class_maps,
    network_style_sort_key,
)
from ..standard import StandardNetwork
from .model import MappedNetwork


def filter_plot_levels(data: object, levels: set[str]) -> object:
    """Filter one mapped object for display without changing stored outputs."""

    if isinstance(data, gpd.GeoDataFrame):
        if "spatial_level" not in data:
            return data
        return data.loc[data["spatial_level"].astype(str).isin(levels)].copy()
    if isinstance(data, xr.Dataset):
        if "spatial_level" not in data.coords:
            return data
        mask = np.isin(data["spatial_level"].values.astype(str), list(levels))
        return data.isel(uid=np.flatnonzero(mask))
    if isinstance(data, MappedNetwork):
        bus = data.bus.loc[
            data.bus["spatial_level"].astype(str).isin(levels)
        ].copy()
        branch_mapping = data.branch_mapping.loc[
            data.branch_mapping["spatial_level"].astype(str).isin(levels)
        ].copy()
        branch_uids = set(branch_mapping["branch_uid"].astype(str))
        branch = data.branch.loc[
            data.branch["uid"].astype(str).isin(branch_uids)
        ].copy()
        bus_uids = set(bus["uid"].astype(str))
        equipment = [frame.loc[
            frame["from_bus_uid"].astype(str).isin(bus_uids)
            & frame["to_bus_uid"].astype(str).isin(bus_uids)
        ].copy() for frame in (data.transformer, data.converter)]
        return MappedNetwork(bus, branch, *equipment, branch_mapping)
    return data


def plot_spatial(
    data: gpd.GeoDataFrame,
    *,
    spatial: gpd.GeoDataFrame | None = None,
    map_crs: str = DEFAULT_MAP_CRS,
    china_inset: bool | None = None,
    **_: object,
) -> Figure:
    """Plot the clipped standard spatial cells."""

    figure, axes = map_axes(spatial, china_inset=china_inset)
    cells = data.to_crs(map_crs)
    levels = cells["spatial_level"].dropna().astype(str).unique().tolist()
    fallback = plt.colormaps["tab10"](np.linspace(0, 1, max(len(levels), 1)))
    colors = {
        level: {
            "province": CELL_COLOR,
            "marine_zone": "#b7c4c7",
        }.get(level, fallback[index])
        for index, level in enumerate(levels)
    }
    for index, axis in enumerate(axes):
        draw_background(axis, spatial, map_crs=map_crs, zorder=0)
        for spatial_level, color in colors.items():
            layer = cells.loc[cells["spatial_level"].eq(spatial_level)]
            if not layer.empty:
                layer.boundary.plot(
                    ax=axis,
                    color=color,
                    linewidth=0.16,
                    alpha=0.58,
                    zorder=2,
                )
        draw_boundaries(axis, spatial, map_crs=map_crs, zorder=3)
        finish_map(
            axis, f"Mapped spatial cells ({len(data):,})" if index == 0 else ""
        )
    return figure


def plot_population(
    data: gpd.GeoDataFrame,
    *,
    spatial: gpd.GeoDataFrame | None = None,
    map_crs: str = DEFAULT_MAP_CRS,
    china_inset: bool | None = None,
    **_: object,
) -> Figure:
    frame = prepare_population_plot(data)
    return continuous_map(
        frame,
        "_value",
        spatial=spatial,
        title="Population mapped to standard spatial cells",
        label="log10(人口 + 1)",
        map_crs=map_crs,
        china_inset=china_inset,
    )


def plot_load(
    data: xr.Dataset,
    *,
    year: int = 2024,
    class_name: str | None = None,
    spatial: gpd.GeoDataFrame | None = None,
    map_crs: str = DEFAULT_MAP_CRS,
    china_inset: bool | None = None,
    **_: object,
) -> PlotResult:
    return timeseries_class_maps(
        data,
        variable="demand_mw",
        spatial=spatial,
        year=year,
        class_name=class_name,
        quantity="load",
        map_crs=map_crs,
        china_inset=china_inset,
    )


def plot_resource(
    data: xr.Dataset,
    *,
    year: int = 2024,
    class_name: str | None = None,
    spatial: gpd.GeoDataFrame | None = None,
    map_crs: str = DEFAULT_MAP_CRS,
    china_inset: bool | None = None,
    **_: object,
) -> PlotResult:
    return timeseries_class_maps(
        data,
        variable="availability_pu",
        spatial=spatial,
        year=year,
        class_name=class_name,
        quantity="resource",
        map_crs=map_crs,
        china_inset=china_inset,
    )


def asset_pie_map(
    data: gpd.GeoDataFrame,
    cells: gpd.GeoDataFrame,
    capacity_column: str,
    title: str,
    spatial: gpd.GeoDataFrame | None,
    map_crs: str,
    china_inset: bool | None,
) -> Figure:
    """Plot class shares as capacity-scaled pies at occupied cell centres."""

    pies, reference, classes = prepare_asset_pies(
        data, cells, capacity_column
    )
    pies = pies.to_crs(map_crs)
    fallback = plt.colormaps["tab10"](np.linspace(0, 1, len(classes)))
    colors = {
        item: CATEGORY_COLORS.get(item, fallback[index])
        for index, item in enumerate(classes)
    }

    figure, axes = map_axes(spatial, china_inset=china_inset)
    for axis_index, axis in enumerate(axes):
        draw_background(axis, spatial, map_crs=map_crs, zorder=0)
        map_width = max(axis.get_xlim()[1] - axis.get_xlim()[0], 1.0)
        patches = {item: [] for item in classes}
        for _, row in pies.iterrows():
            total = float(row["_total"])
            if total <= 0:
                continue
            centre = row.geometry.centroid
            radius = map_width * (
                0.0011 + 0.0062 * np.sqrt(min(float(total) / reference, 1.0))
            )
            start = 0.0
            for item, value in row["_breakdown"].items():
                end = start + 360.0 * float(value) / float(total)
                patches[str(item)].append(
                    Wedge((centre.x, centre.y), radius, start, end)
                )
                start = end
        for item in classes:
            if patches[item]:
                axis.add_collection(PatchCollection(
                    patches[item],
                    facecolor=colors[item],
                    edgecolor="white",
                    linewidth=0.12,
                    alpha=0.82,
                    zorder=3,
                ))
        draw_boundaries(axis, spatial, map_crs=map_crs, zorder=5)
        if axis_index == 0:
            add_asset_legends(
                axis,
                [Patch(facecolor=colors[item], label=class_label(item)) for item in classes],
                reference,
            )
        finish_map(
            axis,
            f"{title}: class share by cell (circle size = total capacity)"
            if axis_index == 0 else "",
        )
    return figure


def prepare_asset_pies(
    data: gpd.GeoDataFrame,
    cells: gpd.GeoDataFrame,
    capacity_column: str,
) -> tuple[gpd.GeoDataFrame, float, list[str]]:
    """Aggregate assets at plotting locations for maps and web payloads."""

    frame = data.loc[data["spatial_uid"].notna()].copy()
    frame["_capacity"] = pd.to_numeric(
        frame[capacity_column], errors="coerce"
    ).fillna(0)
    frame["_class"] = frame["class"].astype("string").fillna("other")
    capacity = frame.groupby(["spatial_uid", "_class"])["_capacity"].sum()
    totals = capacity.groupby(level=0).sum()
    columns = ["spatial_uid", "geometry"]
    if "centre_geometry" in cells:
        columns.append("centre_geometry")
    occupied = cells.loc[
        cells["spatial_uid"].isin(totals.index), columns
    ].copy().set_index("spatial_uid")
    occupied["geometry"] = (
        occupied["centre_geometry"]
        if "centre_geometry" in occupied
        else occupied.geometry.representative_point()
    )
    occupied["_total"] = totals.reindex(occupied.index).fillna(0)
    occupied["_breakdown"] = [
        {
            str(item): float(value)
            for item, value in capacity.loc[spatial_uid].items()
            if value > 0
        }
        for spatial_uid in occupied.index
    ]
    reference = max(float(totals.quantile(0.99)), 1.0)
    classes = capacity.index.get_level_values("_class").unique().astype(str).tolist()
    return occupied.reset_index(), reference, classes


def plot_generator(
    data: gpd.GeoDataFrame,
    *,
    cells: gpd.GeoDataFrame,
    spatial: gpd.GeoDataFrame | None = None,
    map_crs: str = DEFAULT_MAP_CRS,
    china_inset: bool | None = None,
    **_: object,
) -> Figure:
    return asset_pie_map(
        data, cells, "capacity_mw", "Mapped generator", spatial, map_crs,
        china_inset,
    )


def plot_storage(
    data: gpd.GeoDataFrame,
    *,
    cells: gpd.GeoDataFrame,
    spatial: gpd.GeoDataFrame | None = None,
    map_crs: str = DEFAULT_MAP_CRS,
    china_inset: bool | None = None,
    **_: object,
) -> Figure:
    return asset_pie_map(
        data, cells, "power_capacity_mw", "Mapped storage", spatial, map_crs,
        china_inset,
    )


def plot_network(
    data: MappedNetwork,
    *,
    cells: gpd.GeoDataFrame,
    spatial: gpd.GeoDataFrame | None = None,
    map_crs: str = DEFAULT_MAP_CRS,
    china_inset: bool | None = None,
    title: str | None = None,
    **_: object,
) -> Figure:
    """Plot the retained connected branches and junction/station buses."""

    figure, axes = map_axes(spatial, china_inset=china_inset)
    network = StandardNetwork(
        data.bus, data.branch, data.transformer, data.converter
    )
    branches, buses, style_colors = prepare_network_plot(network)
    branches = branches.to_crs(map_crs)
    buses = buses.to_crs(map_crs)
    buses["geometry"] = buses.geometry.representative_point()
    bus_styles = {
        "junction": ("#596267", 0.12, 4),
        "station": ("#d1495b", 0.5, 6),
    }
    for index, axis in enumerate(axes):
        draw_background(axis, spatial, map_crs=map_crs, zorder=0)
        for style, layer in branches.groupby("_style", observed=True):
            layer.plot(
                ax=axis, color=style_colors[str(style)], linewidth=0.36,
                linestyle="--" if str(style).endswith(" DC") else "-",
                alpha=0.72, zorder=3,
            )
        for node_type, (color, size, order) in bus_styles.items():
            layer = buses.loc[buses["_node_type"].eq(node_type)]
            if not layer.empty:
                layer.plot(
                    ax=axis, color=color, markersize=size, marker="o",
                    alpha=0.62, zorder=order,
                )
        draw_boundaries(axis, spatial, map_crs=map_crs, zorder=6)
        if index == 0:
            axis.legend(
                handles=[*[
                    Line2D(
                        [0], [0], color=style_colors[str(style)],
                        linestyle="--" if str(style).endswith(" DC") else "-",
                        linewidth=1.2, label=str(style),
                    )
                    for style in sorted(style_colors, key=network_style_sort_key)
                ],
                    Line2D([0], [0], marker="o", linestyle="none",
                           color="#596267", markersize=4,
                           label="Junction"),
                    Line2D([0], [0], marker="o", linestyle="none",
                           color="#d1495b", markersize=5,
                           label="Station"),
                ],
                loc="lower left",
                bbox_to_anchor=(0.095, 0.07),
                ncol=2,
                frameon=False,
                fontsize=8,
            )
        finish_map(
            axis,
            (
                title or (
                    f"Mapped largest connected network: {len(data.bus):,} buses, "
                    f"{len(data.branch):,} branches"
                )
                if index == 0 else ""
            ),
        )
    return figure


PLOTTERS: dict[str, Callable[..., PlotResult]] = {
    "spatial": plot_spatial,
    "population": plot_population,
    "load": plot_load,
    "resource": plot_resource,
    "network": plot_network,
    "generator": plot_generator,
    "storage": plot_storage,
}
