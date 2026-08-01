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
    junctions = data.nodes[data.nodes["class"].eq("junction")]
    stations = data.nodes[data.nodes["class"].eq("station")]
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
    frame["_class"] = frame["class"].astype("string").fillna("unspecified")
    frame["_subclass"] = (
        frame["subclass"].astype("string").fillna("unspecified")
    )
    frame["_capacity_gw"] = (
        pd.to_numeric(frame[capacity_column], errors="coerce").fillna(0) / 1000
    )
    by_class = (
        frame.groupby("_class", observed=True)["_capacity_gw"].sum().sort_values()
    )
    by_subclass = (
        frame.groupby(["_class", "_subclass"], observed=True)["_capacity_gw"]
        .sum()
        .sort_values()
    )
    class_colors = {
        name: plt.colormaps["tab20"](index / max(len(by_class), 1))
        for index, name in enumerate(by_class.index)
    }

    figure, axes = plt.subplots(
        1,
        2,
        figsize=(14, max(6, 0.28 * len(by_subclass))),
        constrained_layout=True,
    )
    axes[0].barh(
        by_class.index,
        by_class.values,
        color=[class_colors[name] for name in by_class.index],
    )
    subclass_labels = [
        f"{asset_class} / {subclass}"
        for asset_class, subclass in by_subclass.index
    ]
    axes[1].barh(
        subclass_labels,
        by_subclass.values,
        color=[class_colors[asset_class] for asset_class, _ in by_subclass.index],
    )
    for axis, subtitle in zip(
        axes,
        ("Class", "Subclass"),
        strict=True,
    ):
        axis.set_title(subtitle)
        axis.set_xlabel("Capacity (GW)")
        axis.grid(axis="x", color="#d8dcde", linewidth=0.6)
        axis.set_axisbelow(True)
    figure.suptitle(title)
    return figure


def plot_generation(data: gpd.GeoDataFrame, **_: object) -> Figure:
    """Plot generation capacity by class and subclass."""

    return _capacity_plot(
        data,
        "capacity_mw",
        "Generation capacity by class and subclass",
    )


def plot_storage(data: gpd.GeoDataFrame, **_: object) -> Figure:
    """Plot storage power capacity by class and subclass."""

    return _capacity_plot(
        data,
        "power_capacity_mw",
        "Storage power capacity by class and subclass",
    )


def plot_parameter(data: pd.DataFrame, **_: object) -> Figure:
    """Plot parameter-record coverage by standard asset class."""

    frame = data.loc[
        data["class"].notna() & data["parameter_name"].notna(),
        ["class", "parameter_name"],
    ].copy()
    coverage = pd.crosstab(frame["class"], frame["parameter_name"])
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
    axis.set_ylabel("Asset class")
    axis.set_title("Technical-economic parameter coverage")
    figure.colorbar(image, ax=axis, label="Parameter records")
    return figure


def plot_load(data: xr.Dataset, *, year: int = 2024, **_: object) -> Figure:
    """Plot one year of stacked provincial load and national total load."""

    load = data["demand_mw"].sel(time=str(year)).sum("class")
    if load.sizes.get("time", 0) not in {8760, 8784}:
        raise ValueError(f"Load data for {year} is not a complete hourly year.")
    order = np.argsort(load.sum("time").values)[::-1]
    load = load.isel(uid=order)
    labels = data["location"].isel(uid=order).values.astype(str)
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


def plot_population(
    data: gpd.GeoDataFrame,
    *,
    spatial: gpd.GeoDataFrame | None = None,
    **_: object,
) -> Figure:
    """Plot standardized population grid cells on a logarithmic scale."""

    frame = data.copy()
    frame["_display_population"] = np.log10(
        pd.to_numeric(frame["population"], errors="coerce").fillna(0).clip(lower=0) + 1
    )
    figure, axis = plt.subplots(figsize=(11, 8), constrained_layout=True)
    frame.plot(
        ax=axis,
        column="_display_population",
        cmap="magma",
        linewidth=0,
        legend=True,
        legend_kwds={"label": "log10(persons per grid cell + 1)"},
    )
    _province_boundaries(axis, spatial)
    _finish_map(axis, "Population grid")
    return figure


def plot_resource(
    data: xr.Dataset,
    *,
    spatial: gpd.GeoDataFrame | None = None,
    **_: object,
) -> Figure:
    """Plot annual mean availability for each resource class."""

    availability = data["availability_pu"].mean("time")
    classes = availability["class"].values.astype(str)
    geometry = gpd.GeoSeries.from_wkt(data["geometry"].values, crs=data.attrs["crs"])
    columns = min(3, len(classes))
    rows = math.ceil(len(classes) / columns)
    figure, axes = plt.subplots(
        rows,
        columns,
        figsize=(5 * columns, 4.2 * rows),
        constrained_layout=True,
        squeeze=False,
    )
    for axis, resource_class in zip(axes.flat, classes, strict=False):
        layer = gpd.GeoDataFrame(
            {"_availability": availability.sel({"class": resource_class}).values},
            geometry=geometry,
            crs=data.attrs["crs"],
        )
        layer.plot(
            ax=axis,
            column="_availability",
            cmap="viridis",
            vmin=0,
            vmax=1,
            markersize=2,
        )
        _province_boundaries(axis, spatial)
        _finish_map(axis, resource_class)
    for axis in list(axes.flat)[len(classes) :]:
        axis.set_visible(False)
    figure.colorbar(
        plt.cm.ScalarMappable(norm=plt.Normalize(0, 1), cmap="viridis"),
        ax=axes,
        label="Annual mean availability (p.u.)",
    )
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
