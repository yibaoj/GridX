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
    LAND_COLOR,
    PlotResult,
    add_asset_legends,
    continuous_map,
    draw_background,
    draw_boundaries,
    finish_map,
    map_axes,
    timeseries_class_maps,
)
from .model import MappedNetwork


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
            "marine_zone": "#79a8b8",
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
                    linewidth=0.2,
                    alpha=0.85,
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
    frame = data.copy()
    frame["_population"] = np.log10(
        pd.to_numeric(frame["population"], errors="coerce")
        .fillna(0)
        .clip(lower=0)
        + 1
    )
    return continuous_map(
        frame,
        "_population",
        spatial=spatial,
        title="Population mapped to standard spatial cells",
        label="log10(population + 1)",
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

    frame = data.loc[data["spatial_uid"].notna()].copy()
    frame["_capacity"] = pd.to_numeric(
        frame[capacity_column], errors="coerce"
    ).fillna(0)
    frame["_class"] = frame["class"].astype("string").fillna("other")
    capacity = frame.groupby(["spatial_uid", "_class"])["_capacity"].sum()
    totals = capacity.groupby(level=0).sum()
    cells = cells.to_crs(map_crs)
    occupied = cells.loc[
        cells["spatial_uid"].isin(totals.index),
        ["spatial_uid", "geometry"],
    ].set_index("spatial_uid")
    reference = max(float(totals.quantile(0.99)), 1.0)
    classes = (
        capacity.index.get_level_values("_class").unique().astype(str).tolist()
    )
    fallback = plt.colormaps["tab10"](np.linspace(0, 1, len(classes)))
    colors = {
        item: CATEGORY_COLORS.get(item, fallback[index])
        for index, item in enumerate(classes)
    }

    figure, axes = map_axes(spatial, china_inset=china_inset)
    for axis_index, axis in enumerate(axes):
        draw_background(axis, spatial, map_crs=map_crs, zorder=0)
        cells.boundary.plot(
            ax=axis, color=CELL_COLOR, linewidth=0.08, alpha=0.35, zorder=1
        )
        map_width = max(axis.get_xlim()[1] - axis.get_xlim()[0], 1.0)
        patches = {item: [] for item in classes}
        for spatial_uid, total in totals.items():
            if total <= 0 or spatial_uid not in occupied.index:
                continue
            centre = occupied.at[spatial_uid, "geometry"].centroid
            radius = map_width * (
                0.0011 + 0.0062 * np.sqrt(min(float(total) / reference, 1.0))
            )
            start = 0.0
            for item, value in capacity.loc[spatial_uid].items():
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
                [Patch(facecolor=colors[item], label=item) for item in classes],
                reference,
            )
        finish_map(
            axis,
            f"{title}: class share by cell (circle size = total capacity)"
            if axis_index == 0 else "",
        )
    return figure


def plot_generation(
    data: gpd.GeoDataFrame,
    *,
    cells: gpd.GeoDataFrame,
    spatial: gpd.GeoDataFrame | None = None,
    map_crs: str = DEFAULT_MAP_CRS,
    china_inset: bool | None = None,
    **_: object,
) -> Figure:
    return asset_pie_map(
        data, cells, "capacity_mw", "Mapped generation", spatial, map_crs,
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
    **_: object,
) -> Figure:
    """Plot the retained connected network and its associated cells."""

    branch_cells = set(
        data.branch_mapping.loc[
            data.branch_mapping["spatial_uid"].notna(), "spatial_uid"
        ].astype(str)
    )
    node_cells = set(data.nodes["spatial_uid"].dropna().astype(str))
    cells = cells.to_crs(map_crs)
    branch_area = cells.loc[cells["spatial_uid"].astype(str).isin(branch_cells)]
    node_area = cells.loc[cells["spatial_uid"].astype(str).isin(node_cells)]

    figure, axes = map_axes(
        spatial, figsize=(13, 10), china_inset=china_inset
    )
    branches = data.branches.to_crs(map_crs)
    node_layers = {}
    for node_class, color, size, order in (
        ("junction", "#596267", 0.1, 4),
        ("station", "#d1495b", 0.45, 5),
    ):
        node_layer = data.nodes.loc[
            data.nodes["class"].eq(node_class)
        ].to_crs(map_crs)
        node_layer["geometry"] = node_layer.geometry.representative_point()
        node_layers[node_class] = (node_layer, color, size, order)
    for index, axis in enumerate(axes):
        draw_background(axis, spatial, map_crs=map_crs, zorder=0)
        branch_area.plot(
            ax=axis,
            color="#dcecf3",
            edgecolor="#b8d7e5",
            linewidth=0.12,
            alpha=0.8,
            zorder=1,
        )
        node_area.plot(
            ax=axis,
            color="#f8e6a5",
            edgecolor="#e3c75e",
            linewidth=0.14,
            alpha=0.72,
            zorder=2,
        )
        branches.plot(
            ax=axis,
            color="#276b91",
            linewidth=0.34,
            alpha=0.75,
            zorder=3,
        )
        for node_layer, color, size, order in node_layers.values():
            node_layer.plot(
                ax=axis,
                color=color,
                markersize=size,
                alpha=0.6,
                zorder=order,
            )
        draw_boundaries(axis, spatial, map_crs=map_crs, zorder=6)
        if index == 0:
            axis.legend(
                handles=[
                    Patch(facecolor="#dcecf3", edgecolor="#b8d7e5",
                          label="Branch cells"),
                    Patch(facecolor="#f8e6a5", edgecolor="#e3c75e",
                          label="Node cells"),
                    Line2D([0], [0], color="#276b91", label="Branches"),
                    Line2D([0], [0], marker="o", linestyle="none",
                           color="#596267", markersize=4,
                           label="Junction nodes"),
                    Line2D([0], [0], marker="o", linestyle="none",
                           color="#d1495b", markersize=5,
                           label="Station nodes"),
                ],
                loc="lower left",
                ncol=2,
                frameon=False,
                fontsize=8,
            )
        finish_map(
            axis,
            (
                f"Mapped largest connected network: {len(data.nodes):,} nodes, "
                f"{len(data.branches):,} branches"
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
    "generation": plot_generation,
    "storage": plot_storage,
}
