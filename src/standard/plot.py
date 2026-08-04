"""Shared map style and plots for standard datasets."""

from __future__ import annotations

from collections.abc import Callable

import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd
from shapely.geometry import Polygon, box
import xarray as xr

from .geometry import polygonal_geometry
from .schema import NetworkData


PlotResult = Figure | dict[str, Figure]

DEFAULT_MAP_CRS = (
    "+proj=aea +lat_1=25 +lat_2=47 +lat_0=0 +lon_0=105 "
    "+datum=WGS84 +units=m +no_defs"
)
CHINA_MAIN_BOUNDS = (73.0, 17.0, 136.5, 54.5)
CHINA_INSET_BOUNDS = (107.0, 2.0, 120.5, 23.0)
LAND_COLOR = "#f1f2f2"
MARINE_COLOR = "#e8f2f5"
BOUNDARY_COLOR = "#9aa2a6"
MARINE_BOUNDARY_COLOR = "#79a8b8"
CELL_COLOR = "#c7ccce"
CONTINUOUS_CMAP = "viridis"
CATEGORY_COLORS = {
    "bioenergy": "#59a14f",
    "coal": "#4d5356",
    "gas": "#f28e2b",
    "geothermal": "#9c6ade",
    "nuclear": "#d1495b",
    "hydropower": "#4e79a7",
    "solar": "#edc948",
    "wind": "#2a9d8f",
    "other": "#9c755f",
    "pumped_storage": "#4e79a7",
    "battery_storage": "#e07a5f",
    "compressed_air_storage": "#76b7b2",
    "thermal_storage": "#edc948",
    "capacitor_storage": "#9c6ade",
}
CATEGORY_MARKERS = {
    "bioenergy": "P",
    "coal": "s",
    "gas": "D",
    "geothermal": "h",
    "nuclear": "*",
    "hydropower": "v",
    "solar": "^",
    "wind": "X",
    "other": "o",
    "pumped_storage": "v",
    "battery_storage": "s",
    "compressed_air_storage": "D",
    "thermal_storage": "^",
    "capacitor_storage": "P",
}


def province_frame(spatial: gpd.GeoDataFrame | None) -> gpd.GeoDataFrame | None:
    if spatial is None or spatial.empty:
        return None
    if "level" in spatial and spatial["level"].eq("province").any():
        spatial = spatial.loc[spatial["level"].eq("province")]
    result = spatial.copy()
    result["geometry"] = result.geometry.map(polygonal_geometry)
    return result.loc[~result.geometry.is_empty]


def marine_frame(spatial: gpd.GeoDataFrame | None) -> gpd.GeoDataFrame | None:
    if spatial is None or spatial.empty or "level" not in spatial:
        return None
    result = spatial.loc[spatial["level"].eq("marine_zone")].copy()
    if result.empty:
        return None
    result["geometry"] = result.geometry.map(polygonal_geometry)
    return result.loc[~result.geometry.is_empty]


def map_axes(
    spatial: gpd.GeoDataFrame | None,
    *,
    figsize: tuple[float, float] = (11, 8),
    china_inset: bool | None = None,
) -> tuple[Figure, list[plt.Axes]]:
    """Create a map canvas, adding a South China Sea inset only for China."""

    figure, main_axis = plt.subplots(figsize=figsize, constrained_layout=True)
    axes = [main_axis]
    use_inset = _is_national_china(spatial) and china_inset is not False
    main_axis._map_lonlat_bounds = CHINA_MAIN_BOUNDS if use_inset else None
    main_axis._map_is_inset = False
    if use_inset:
        inset_axis = main_axis.inset_axes([0.815, 0.045, 0.145, 0.27])
        inset_axis._map_lonlat_bounds = CHINA_INSET_BOUNDS
        inset_axis._map_is_inset = True
        axes.append(inset_axis)
    return figure, axes


def _is_national_china(spatial: gpd.GeoDataFrame | None) -> bool:
    region = province_frame(spatial)
    if region is None or len(region) < 30 or "adcode" not in region:
        return False
    adcodes = set(region["adcode"].astype("string").dropna())
    if not {"110000", "310000", "460000", "650000"}.issubset(adcodes):
        return False
    minx, miny, maxx, maxy = region.to_crs("EPSG:4326").total_bounds
    return minx < 75 and miny < 10 and maxx > 130 and maxy > 50


def _set_axis_extent(
    axis: plt.Axes,
    region: gpd.GeoDataFrame,
    map_crs: str,
) -> None:
    bounds = getattr(axis, "_map_lonlat_bounds", None)
    projected = (
        region.to_crs(map_crs)
        if bounds is None
        else gpd.GeoSeries(
            [_densified_lonlat_box(bounds)], crs="EPSG:4326"
        ).to_crs(map_crs)
    )
    if projected.empty:
        return
    minx, miny, maxx, maxy = projected.total_bounds
    padding = max(maxx - minx, maxy - miny) * (
        0.04 if getattr(axis, "_map_is_inset", False) else 0.025
    )
    axis._map_projected_bounds = (
        minx - padding,
        maxx + padding,
        miny - padding,
        maxy + padding,
    )
    axis.set_xlim(axis._map_projected_bounds[:2])
    axis.set_ylim(axis._map_projected_bounds[2:])


def _densified_lonlat_box(
    bounds: tuple[float, float, float, float],
    samples: int = 181,
) -> Polygon:
    """Represent a lon/lat extent accurately after a nonlinear projection."""

    minx, miny, maxx, maxy = bounds
    xs = np.linspace(minx, maxx, samples)
    ys = np.linspace(miny, maxy, samples)
    coordinates = (
        [(x, miny) for x in xs]
        + [(maxx, y) for y in ys[1:]]
        + [(x, maxy) for x in xs[-2::-1]]
        + [(minx, y) for y in ys[-2:0:-1]]
    )
    return Polygon(coordinates)


def _nice_capacity(value: float) -> float:
    exponent = np.floor(np.log10(max(value, 1e-12)))
    scale = 10.0 ** exponent
    fraction = value / scale
    factor = 1 if fraction < 1.5 else 2 if fraction < 3.5 else 5
    return float(factor * scale)


def _format_capacity(value: float) -> str:
    if value >= 1000:
        return f"{value / 1000:g} GW"
    return f"{value:g} MW"


def add_asset_legends(
    axis: plt.Axes,
    class_handles: list,
    capacity_reference: float,
) -> None:
    """Add separate class-color and total-capacity legends to an asset map."""

    class_legend = axis.legend(
        handles=class_handles,
        loc="lower left",
        ncol=2,
        frameon=False,
        fontsize=8,
        title="Class",
    )
    axis.add_artist(class_legend)
    values = sorted({
        _nice_capacity(capacity_reference * fraction)
        for fraction in (0.1, 0.5, 1.0)
    })
    size_handles = [
        Line2D(
            [0],
            [0],
            marker="o",
            linestyle="none",
            markerfacecolor="none",
            markeredgecolor="#596267",
            markeredgewidth=0.8,
            markersize=3.5 + 4.5 * np.sqrt(min(value / capacity_reference, 1)),
            label=_format_capacity(value),
        )
        for value in values
    ]
    axis.legend(
        handles=size_handles,
        loc="upper left",
        frameon=False,
        fontsize=8,
        title="Total capacity",
        labelspacing=0.8,
    )


def draw_background(
    axis: plt.Axes,
    spatial: gpd.GeoDataFrame | None,
    *,
    map_crs: str = DEFAULT_MAP_CRS,
    fill: bool = True,
    zorder: int = 0,
) -> None:
    region = province_frame(spatial)
    if region is None:
        return
    region = region.to_crs(map_crs)
    region.plot(
        ax=axis,
        color=LAND_COLOR if fill else "none",
        edgecolor=BOUNDARY_COLOR,
        linewidth=0.45,
        zorder=zorder,
    )
    _set_axis_extent(axis, province_frame(spatial), map_crs)


def draw_boundaries(
    axis: plt.Axes,
    spatial: gpd.GeoDataFrame | None,
    *,
    map_crs: str = DEFAULT_MAP_CRS,
    zorder: int = 5,
) -> None:
    marine = marine_frame(spatial)
    if marine is not None:
        marine.to_crs(map_crs).boundary.plot(
            ax=axis,
            color=MARINE_BOUNDARY_COLOR,
            linewidth=0.55,
            alpha=0.9,
            zorder=zorder,
        )
    region = province_frame(spatial)
    if region is not None:
        region = region.to_crs(map_crs)
        region.boundary.plot(
            ax=axis,
            color=BOUNDARY_COLOR,
            linewidth=0.45,
            alpha=0.9,
            zorder=zorder,
        )


def finish_map(axis: plt.Axes, title: str = "") -> None:
    if title:
        axis.set_title(title, color="#263238", pad=8, fontsize=12)
    bounds = getattr(axis, "_map_projected_bounds", None)
    if bounds is not None:
        axis.set_xlim(bounds[:2])
        axis.set_ylim(bounds[2:])
    if getattr(axis, "_map_is_inset", False):
        axis.set_xticks([])
        axis.set_yticks([])
        for spine in axis.spines.values():
            spine.set_visible(True)
            spine.set_color(BOUNDARY_COLOR)
            spine.set_linewidth(0.7)
    else:
        axis.set_axis_off()
    axis.set_aspect("equal")


def continuous_map(
    frame: gpd.GeoDataFrame,
    value_column: str,
    *,
    spatial: gpd.GeoDataFrame | None,
    title: str,
    label: str,
    map_crs: str = DEFAULT_MAP_CRS,
    china_inset: bool | None = None,
    vmin: float | None = None,
    vmax: float | None = None,
) -> Figure:
    figure, axes = map_axes(spatial, china_inset=china_inset)
    frame = frame.to_crs(map_crs)
    for index, axis in enumerate(axes):
        draw_background(axis, spatial, map_crs=map_crs, zorder=0)
        existing_axes = set(figure.axes)
        frame.plot(
            ax=axis,
            column=value_column,
            cmap=CONTINUOUS_CMAP,
            vmin=vmin,
            vmax=vmax,
            linewidth=0,
            edgecolor="none",
            markersize=5,
            legend=index == 0,
            legend_kwds={
                "label": label,
                "location": "left",
                "shrink": 0.48,
                "aspect": 24,
                "pad": 0.015,
            },
            missing_kwds={"color": "#e4e6e6"},
            zorder=2,
        )
        if index == 0:
            for colorbar_axis in set(figure.axes).difference(existing_axes):
                colorbar_axis.tick_params(labelsize=7, length=2)
                colorbar_axis.yaxis.label.set_size(8)
        draw_boundaries(axis, spatial, map_crs=map_crs, zorder=4)
        finish_map(axis, title if index == 0 else "")
    return figure


def timeseries_class_maps(
    data: xr.Dataset,
    *,
    variable: str,
    spatial: gpd.GeoDataFrame | None,
    year: int,
    class_name: str | None,
    quantity: str,
    map_crs: str = DEFAULT_MAP_CRS,
    china_inset: bool | None = None,
) -> PlotResult:
    """Plot one map per class from a time-by-location dataset."""

    classes = (
        [str(class_name)]
        if class_name
        else data["class"].values.astype(str).tolist()
    )
    unknown = set(classes).difference(data["class"].values.astype(str))
    if unknown:
        raise KeyError(f"Unknown class values: {sorted(unknown)}")
    geometry = _xarray_geometry(data)
    figures = {}
    for item in classes:
        values = data[variable].sel(time=str(year), **{"class": item})
        if values.sizes.get("time", 0) == 0:
            raise ValueError(f"No {year} data are available for class={item!r}.")
        if quantity == "load":
            display = values.sum("time").compute().values / 1e6
            label = f"Annual {item} demand (TWh)"
            title = f"{item}: annual electricity demand, {year}"
            limits = (None, None)
        else:
            display = values.mean("time").compute().values
            label = f"Annual mean {item} capacity factor (p.u.)"
            title = f"{item}: annual mean capacity factor, {year}"
            limits = (0.0, 1.0)
        frame = gpd.GeoDataFrame(
            {"_value": display},
            geometry=geometry,
            crs=data.attrs["crs"],
        )
        figures[item] = continuous_map(
            frame,
            "_value",
            spatial=spatial,
            title=title,
            label=label,
            map_crs=map_crs,
            china_inset=china_inset,
            vmin=limits[0],
            vmax=limits[1],
        )
    return figures[classes[0]] if class_name else figures


def _xarray_geometry(data: xr.Dataset) -> gpd.GeoSeries:
    geometry = gpd.GeoSeries.from_wkt(
        data["geometry"].values,
        crs=str(data.attrs["crs"]),
    )
    if not geometry.geom_type.eq("Point").all():
        return geometry
    x = np.array([point.x for point in geometry])
    y = np.array([point.y for point in geometry])
    width = _coordinate_step(x)
    height = _coordinate_step(y)
    return gpd.GeoSeries(
        [
            box(
                point.x - width / 2,
                point.y - height / 2,
                point.x + width / 2,
                point.y + height / 2,
            )
            for point in geometry
        ],
        crs=geometry.crs,
    )


def _coordinate_step(values: np.ndarray) -> float:
    unique = np.unique(np.round(values, 8))
    differences = np.diff(unique)
    positive = differences[differences > 1e-8]
    return float(np.median(positive)) if len(positive) else 0.25


def plot_spatial(
    data: gpd.GeoDataFrame,
    *,
    map_crs: str = DEFAULT_MAP_CRS,
    china_inset: bool | None = None,
    **_: object,
) -> Figure:
    """Plot standardized land and marine spatial units."""

    figure, axes = map_axes(data, china_inset=china_inset)
    provinces = province_frame(data).to_crs(map_crs)
    marine = marine_frame(data)
    if marine is not None:
        marine = marine.to_crs(map_crs)
    other_levels = data.loc[
        ~data["level"].isin(["province", "marine_zone"])
    ].to_crs(map_crs)
    for index, axis in enumerate(axes):
        if marine is not None:
            marine.plot(
                ax=axis,
                color=MARINE_COLOR,
                edgecolor=MARINE_BOUNDARY_COLOR,
                linewidth=0.55,
            )
        provinces.plot(
            ax=axis,
            color=LAND_COLOR,
            edgecolor=BOUNDARY_COLOR,
            linewidth=0.55,
        )
        if not other_levels.empty:
            other_levels.boundary.plot(
                ax=axis,
                color="#8c6d9c",
                linewidth=0.65,
                alpha=0.9,
            )
        draw_background(axis, data, map_crs=map_crs, fill=False, zorder=2)
        finish_map(
            axis,
            f"Standard spatial units ({len(data):,})" if index == 0 else "",
        )
    return figure


def plot_network(
    data: NetworkData,
    *,
    spatial: gpd.GeoDataFrame | None = None,
    map_crs: str = DEFAULT_MAP_CRS,
    china_inset: bool | None = None,
    **_: object,
) -> Figure:
    """Plot all standardized network nodes and branches."""

    figure, axes = map_axes(
        spatial, figsize=(13, 10), china_inset=china_inset
    )
    branches = data.branches.to_crs(map_crs)
    node_layers = {}
    for node_class, color, size, order in (
        ("junction", "#596267", 0.1, 3),
        ("station", "#d1495b", 0.45, 4),
    ):
        node_layer = data.nodes.loc[
            data.nodes["class"].eq(node_class)
        ].to_crs(map_crs)
        node_layer["geometry"] = node_layer.geometry.representative_point()
        node_layers[node_class] = (node_layer, color, size, order)
    for index, axis in enumerate(axes):
        draw_background(axis, spatial, map_crs=map_crs, zorder=0)
        branches.plot(
            ax=axis,
            color="#4e79a7",
            linewidth=0.3,
            alpha=0.62,
            zorder=2,
        )
        for node_layer, color, size, order in node_layers.values():
            node_layer.plot(
                ax=axis,
                color=color,
                markersize=size,
                alpha=0.58,
                zorder=order,
            )
        if index == 0:
            axis.legend(
                handles=[
                    Line2D([0], [0], color="#4e79a7", label="Branches"),
                    Line2D([0], [0], marker="o", linestyle="none",
                           color="#596267", markersize=4,
                           label="Junction nodes"),
                    Line2D([0], [0], marker="o", linestyle="none",
                           color="#d1495b", markersize=5,
                           label="Station nodes"),
                ],
                loc="lower left",
                frameon=False,
            )
        finish_map(
            axis,
            (
                f"Standard network: {len(data.nodes):,} nodes, "
                f"{len(data.branches):,} branches"
                if index == 0 else ""
            ),
        )
    return figure


def asset_point_map(
    data: gpd.GeoDataFrame,
    capacity_column: str,
    title: str,
    spatial: gpd.GeoDataFrame | None,
    map_crs: str,
    china_inset: bool | None,
) -> Figure:
    """Plot point assets with class-specific colors and markers."""

    capacity = pd.to_numeric(data[capacity_column], errors="coerce")
    frame = data.loc[data.geometry.notna() & capacity.gt(0)].copy()
    frame["_class"] = frame["class"].astype("string").fillna("other")
    frame["_capacity"] = capacity.loc[frame.index]
    reference = max(float(frame["_capacity"].quantile(0.99)), 1.0)
    frame["_marker_size"] = 2 + 24 * np.sqrt(
        (frame["_capacity"] / reference).clip(0, 1)
    )
    frame = frame.to_crs(map_crs)
    figure, axes = map_axes(spatial, china_inset=china_inset)
    classes = frame["_class"].value_counts().index.astype(str)
    fallback = plt.colormaps["tab10"](np.linspace(0, 1, len(classes)))
    handles = []
    for class_index, item in enumerate(classes):
        color = CATEGORY_COLORS.get(item, fallback[class_index])
        marker = CATEGORY_MARKERS.get(item, "o")
        handles.append(Line2D(
            [0], [0], marker=marker, linestyle="none", markerfacecolor=color,
            markeredgecolor="white", markersize=7, label=item,
        ))
    for axis_index, axis in enumerate(axes):
        draw_background(axis, spatial, map_crs=map_crs, zorder=0)
        for class_index, item in enumerate(classes):
            color = CATEGORY_COLORS.get(item, fallback[class_index])
            marker = CATEGORY_MARKERS.get(item, "o")
            layer = frame.loc[frame["_class"].eq(item)].sort_values("_capacity")
            layer.plot(
                ax=axis,
                color=color,
                marker=marker,
                markersize=layer["_marker_size"],
                alpha=0.65,
                edgecolor="white",
                linewidth=0.15,
                zorder=2 + class_index / 100,
            )
        draw_boundaries(axis, spatial, map_crs=map_crs, zorder=4)
        if axis_index == 0:
            add_asset_legends(axis, handles, reference)
        finish_map(
            axis,
            f"{title} ({frame['_capacity'].sum() / 1000:,.1f} GW)"
            if axis_index == 0 else "",
        )
    return figure


def plot_generation(
    data: gpd.GeoDataFrame,
    *,
    spatial: gpd.GeoDataFrame | None = None,
    map_crs: str = DEFAULT_MAP_CRS,
    china_inset: bool | None = None,
    **_: object,
) -> Figure:
    return asset_point_map(
        data, "capacity_mw", "Generation assets by class", spatial, map_crs,
        china_inset,
    )


def plot_storage(
    data: gpd.GeoDataFrame,
    *,
    spatial: gpd.GeoDataFrame | None = None,
    map_crs: str = DEFAULT_MAP_CRS,
    china_inset: bool | None = None,
    **_: object,
) -> Figure:
    return asset_point_map(
        data, "power_capacity_mw", "Storage assets by class", spatial, map_crs,
        china_inset,
    )


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
        pd.to_numeric(frame["population"], errors="coerce").fillna(0).clip(lower=0)
        + 1
    )
    return continuous_map(
        frame,
        "_population",
        spatial=spatial,
        title="Population by standardized source cell",
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


def plot_parameter(data: pd.DataFrame, **_: object) -> Figure:
    """Plot parameter coverage by standardized asset class."""

    frame = data.loc[
        data["class"].notna() & data["parameter_name"].notna(),
        ["class", "parameter_name"],
    ]
    coverage = pd.crosstab(frame["class"], frame["parameter_name"])
    figure, axis = plt.subplots(figsize=(12, 6.5), constrained_layout=True)
    image = axis.imshow(coverage, cmap=CONTINUOUS_CMAP, aspect="auto")
    axis.set_xticks(range(len(coverage.columns)), coverage.columns, rotation=60)
    axis.set_yticks(range(len(coverage.index)), coverage.index)
    axis.set_title("Technical-economic parameter coverage")
    figure.colorbar(image, ax=axis, label="Parameter records")
    return figure


PLOTTERS: dict[str, Callable[..., PlotResult]] = {
    "spatial": plot_spatial,
    "network": plot_network,
    "generation": plot_generation,
    "storage": plot_storage,
    "parameter": plot_parameter,
    "load": plot_load,
    "population": plot_population,
    "resource": plot_resource,
}
