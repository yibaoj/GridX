"""Representative quality-control plots for standard datasets."""

from __future__ import annotations

from collections.abc import Callable
import math

import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
import numpy as np
import pandas as pd
import xarray as xr

from .schema import NetworkData


def _finish_map(axis: plt.Axes, title: str) -> None:
    axis.set_title(title)
    axis.set_axis_off()
    axis.set_aspect("equal")


def _province_boundaries(
    axis: plt.Axes,
    spatial: gpd.GeoDataFrame | None,
    *,
    zorder: int = 5,
) -> None:
    if spatial is not None and not spatial.empty:
        spatial.boundary.plot(
            ax=axis,
            color="#5c6468",
            linewidth=0.35,
            alpha=0.75,
            zorder=zorder,
        )


def plot_spatial(data: gpd.GeoDataFrame, **_: object) -> Figure:
    """Plot every canonical spatial unit."""

    figure, axis = plt.subplots(figsize=(11, 8), constrained_layout=True)
    data.plot(
        ax=axis,
        color="#dce8e5",
        edgecolor="#52636a",
        linewidth=0.45 if len(data) < 500 else 0.12,
    )
    _finish_map(axis, f"Canonical spatial units ({len(data):,})")
    return figure


def plot_network(
    data: NetworkData,
    *,
    spatial: gpd.GeoDataFrame | None = None,
    **_: object,
) -> Figure:
    """Plot every canonical network node and branch."""

    figure, axis = plt.subplots(figsize=(13, 10), constrained_layout=True)
    _province_boundaries(axis, spatial, zorder=1)
    data.branches.plot(
        ax=axis,
        color="#397dad",
        linewidth=0.22,
        alpha=0.45,
        label="Branches",
        zorder=2,
    )
    junctions = data.nodes[data.nodes["type"].eq("junction")]
    stations = data.nodes[data.nodes["type"].eq("station")]
    junctions.plot(
        ax=axis,
        color="#20272b",
        markersize=0.25,
        alpha=0.22,
        label="Junction nodes",
        zorder=3,
    )
    stations.plot(
        ax=axis,
        color="#d94832",
        edgecolor="none",
        markersize=2.0,
        alpha=0.7,
        label="Station nodes",
        zorder=4,
    )
    handles, labels = axis.get_legend_handles_labels()
    unique_handles = dict(zip(labels, handles, strict=True))
    axis.legend(
        unique_handles.values(),
        unique_handles.keys(),
        loc="lower left",
        frameon=False,
        markerscale=4,
    )
    _finish_map(
        axis,
        (
            f"Canonical power network: {len(data.nodes):,} nodes and "
            f"{len(data.branches):,} branches"
        ),
    )
    return figure


def _capacity_plot(
    data: gpd.GeoDataFrame,
    capacity_column: str,
    title: str,
) -> Figure:
    frame = data.loc[data[capacity_column].notna()].copy()
    frame["_type"] = frame["type"].astype("string").fillna("unspecified")
    frame["_technology"] = (
        frame["technology"].astype("string").fillna("unspecified")
    )
    frame["_capacity_gw"] = (
        pd.to_numeric(frame[capacity_column], errors="coerce").fillna(0) / 1000
    )
    by_type = (
        frame.groupby("_type", observed=True)["_capacity_gw"].sum().sort_values()
    )
    by_technology = (
        frame.groupby(["_type", "_technology"], observed=True)["_capacity_gw"]
        .sum()
        .sort_values()
    )
    type_colors = {
        name: plt.colormaps["tab20"](index / max(len(by_type), 1))
        for index, name in enumerate(by_type.index)
    }

    figure, axes = plt.subplots(
        1,
        2,
        figsize=(14, max(6, 0.28 * len(by_technology))),
        constrained_layout=True,
    )
    axes[0].barh(
        by_type.index,
        by_type.values,
        color=[type_colors[name] for name in by_type.index],
    )
    technology_labels = [
        f"{asset_type} / {technology}"
        for asset_type, technology in by_technology.index
    ]
    axes[1].barh(
        technology_labels,
        by_technology.values,
        color=[type_colors[asset_type] for asset_type, _ in by_technology.index],
    )
    for axis, subtitle in zip(
        axes,
        ("Primary type", "Technology"),
        strict=True,
    ):
        axis.set_title(subtitle)
        axis.set_xlabel("Capacity (GW)")
        axis.grid(axis="x", color="#d8dcde", linewidth=0.6)
        axis.set_axisbelow(True)
    figure.suptitle(title)
    return figure


def plot_generation(data: gpd.GeoDataFrame, **_: object) -> Figure:
    """Plot generation capacity by primary type and technology."""

    return _capacity_plot(
        data,
        "capacity_mw",
        "Generation capacity by type and technology",
    )


def plot_storage(data: gpd.GeoDataFrame, **_: object) -> Figure:
    """Plot storage power capacity by primary type and technology."""

    return _capacity_plot(
        data,
        "power_capacity_mw",
        "Storage power capacity by type and technology",
    )


def plot_parameter(data: pd.DataFrame, **_: object) -> Figure:
    """Plot parameter-record coverage by standard asset type."""

    frame = data.loc[
        data["type"].notna() & data["parameter_name"].notna(),
        ["type", "parameter_name"],
    ].copy()
    coverage = pd.crosstab(frame["type"], frame["parameter_name"])
    coverage = coverage.loc[
        coverage.sum(axis=1).sort_values(ascending=False).index,
        coverage.sum(axis=0).sort_values(ascending=False).index,
    ]
    figure, axis = plt.subplots(
        figsize=(max(10, 0.42 * len(coverage.columns)), 6.5),
        constrained_layout=True,
    )
    image = axis.imshow(coverage, cmap="YlGnBu", aspect="auto")
    axis.set_xticks(range(len(coverage.columns)), coverage.columns, rotation=60)
    axis.set_yticks(range(len(coverage.index)), coverage.index)
    axis.set_xlabel("Parameter")
    axis.set_ylabel("Asset type")
    axis.set_title("Technical-economic parameter coverage")
    figure.colorbar(image, ax=axis, label="Parameter records")
    return figure


def plot_load(data: xr.Dataset, *, year: int = 2024, **_: object) -> Figure:
    """Plot one year of stacked provincial load and national total load."""

    load = data["demand_mw"].sel(time=str(year))
    if load.sizes.get("time", 0) not in {8760, 8784}:
        raise ValueError(f"Load data for {year} is not a complete hourly year.")
    order = np.argsort(load.sum("time").values)[::-1]
    load = load.isel(uid=order)
    labels = (
        data["source_region_name"].isel(uid=order).values.astype(str)
        if "source_region_name" in data.coords
        else data["region_name"].isel(uid=order).values.astype(str)
    )
    palettes = [
        plt.colormaps["tab20"](np.linspace(0, 1, 20)),
        plt.colormaps["tab20b"](np.linspace(0, 1, 20)),
    ]
    colors = np.vstack(palettes)[: load.sizes["uid"]]
    time = pd.DatetimeIndex(load["time"].values)
    values_gw = load.values.T / 1000

    figure, axis = plt.subplots(figsize=(16, 8), constrained_layout=True)
    axis.stackplot(time, values_gw, labels=labels, colors=colors, alpha=0.9)
    axis.plot(
        time,
        values_gw.sum(axis=0),
        color="black",
        linewidth=1.4,
        label="National total",
        zorder=5,
    )
    axis.set_title(f"Provincial hourly load composition, {year}")
    axis.set_ylabel("Load (GW)")
    axis.set_xlim(time[0], time[-1])
    axis.margins(x=0)
    axis.grid(axis="y", color="#d8dcde", linewidth=0.6)
    axis.set_axisbelow(True)
    axis.legend(
        loc="upper left",
        bbox_to_anchor=(1.005, 1),
        ncol=2,
        frameon=False,
        fontsize=7,
    )
    return figure


def _display_raster(
    axis: plt.Axes,
    values: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    *,
    cmap: str,
    maximum_pixels: int = 1_600,
) -> object:
    y_step = max(1, math.ceil(values.shape[-2] / maximum_pixels))
    x_step = max(1, math.ceil(values.shape[-1] / maximum_pixels))
    sampled = values[::y_step, ::x_step]
    return axis.imshow(
        sampled,
        extent=[float(x.min()), float(x.max()), float(y.min()), float(y.max())],
        origin="upper",
        cmap=cmap,
        interpolation="nearest",
        aspect="equal",
    )


def plot_population(
    data: xr.Dataset,
    *,
    spatial: gpd.GeoDataFrame | None = None,
    **_: object,
) -> Figure:
    """Plot the population raster on a logarithmic display scale."""

    values = np.log10(np.clip(data["population"].values, 0, None) + 1)
    figure, axis = plt.subplots(figsize=(11, 8), constrained_layout=True)
    image = _display_raster(
        axis,
        values,
        data["x"].values,
        data["y"].values,
        cmap="magma",
    )
    _province_boundaries(axis, spatial)
    figure.colorbar(image, ax=axis, label="log10(persons per source cell + 1)")
    _finish_map(axis, "Population raster")
    return figure


def plot_resource(
    data: xr.Dataset,
    *,
    spatial: gpd.GeoDataFrame | None = None,
    **_: object,
) -> Figure:
    """Plot annual mean availability for each weather-dependent technology."""

    availability = data["availability_pu"].mean("time")
    technologies = availability["technology"].values.astype(str)
    columns = min(3, len(technologies))
    rows = math.ceil(len(technologies) / columns)
    figure, axes = plt.subplots(
        rows,
        columns,
        figsize=(5 * columns, 4.2 * rows),
        constrained_layout=True,
        squeeze=False,
    )
    image = None
    for axis, technology in zip(axes.flat, technologies, strict=False):
        layer = availability.sel(technology=technology)
        image = _display_raster(
            axis,
            layer.values,
            layer["x"].values,
            layer["y"].values,
            cmap="viridis",
        )
        image.set_clim(0, 1)
        _province_boundaries(axis, spatial)
        _finish_map(axis, technology)
    for axis in list(axes.flat)[len(technologies) :]:
        axis.set_visible(False)
    if image is not None:
        figure.colorbar(image, ax=axes, label="Annual mean availability (p.u.)")
    figure.suptitle("Weather-dependent resource availability")
    return figure


PLOTTERS: dict[str, Callable[..., Figure]] = {
    "spatial": plot_spatial,
    "network": plot_network,
    "generation": plot_generation,
    "storage": plot_storage,
    "parameter": plot_parameter,
    "load": plot_load,
    "population": plot_population,
    "resource": plot_resource,
}
