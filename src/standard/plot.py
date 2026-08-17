"""Shared map style and plots for standard datasets."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import warnings

import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize
import numpy as np
import pandas as pd
from shapely.geometry import Polygon, box
import xarray as xr

from .geometry import polygonal_geometry
from .model import StandardNetwork


PlotResult = Figure | dict[str, Figure]

DEFAULT_MAP_CRS = (
    "+proj=aea +lat_1=25 +lat_2=47 +lat_0=0 +lon_0=105 "
    "+datum=WGS84 +units=m +no_defs"
)
CHINA_MAIN_BOUNDS = (73.0, 17.0, 136.5, 54.5)
CHINA_INSET_BOUNDS = (107.0, 2.0, 120.5, 23.0)
LAND_COLOR = "#f2f3f1"
MARINE_COLOR = "#f7f9f9"
BOUNDARY_COLOR = "#adb3b0"
OUTER_BOUNDARY_COLOR = "#737c77"
MARINE_BOUNDARY_COLOR = "#b5c2c6"
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
CATEGORY_LABELS_ZH = {
    "bioenergy": "生物质",
    "coal": "煤电",
    "gas": "天然气",
    "geothermal": "地热",
    "nuclear": "核电",
    "hydropower": "水电",
    "solar": "光伏",
    "wind": "风电",
    "other": "其他",
    "battery": "电化学",
    "battery_storage": "电化学",
    "pumped_hydro": "抽水蓄能",
    "pumped_storage": "抽水蓄能",
    "thermal_storage": "热储能",
    "compressed_air": "压缩空气",
    "compressed_air_storage": "压缩空气",
    "capacitor_storage": "超级电容",
    "onshore": "陆上风电",
    "offshore_fixed": "海上风电",
    "offshore_floating": "海上风电",
    "offshore_unspecified": "海上风电",
    "run_of_river": "径流水电",
    "utility_scale_pv": "光伏",
    "electric_load": "电力负荷",
}
NETWORK_VOLTAGE_COLORS = {
    110.0: "#56b4c6",
    220.0: "#4e79a7",
    330.0: "#59a14f",
    500.0: "#f28e2b",
    660.0: "#e15759",
    750.0: "#8f6bb3",
    800.0: "#9c755f",
    1000.0: "#e377a8",
    1100.0: "#bcbd22",
    1150.0: "#76b7b2",
}


def province_frame(spatial: gpd.GeoDataFrame | None) -> gpd.GeoDataFrame | None:
    if spatial is None or spatial.empty:
        return None
    if "level" in spatial:
        provinces = spatial.loc[spatial["level"].eq("province")]
        spatial = (
            provinces
            if not provinces.empty
            else spatial.loc[~spatial["level"].eq("marine_zone")]
        )
    if spatial.empty:
        return None
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


def filter_spatial_levels(
    data: gpd.GeoDataFrame,
    spatial_levels: str | Iterable[str] | None,
    *,
    column: str = "level",
) -> gpd.GeoDataFrame:
    """Select requested spatial levels while preserving the original schema."""

    if spatial_levels is None:
        return data
    levels = (
        [spatial_levels]
        if isinstance(spatial_levels, str)
        else [str(level) for level in spatial_levels]
    )
    if not levels or len(levels) != len(set(levels)):
        raise ValueError("spatial_levels must contain unique values.")
    if column not in data:
        raise KeyError(f"Spatial data have no {column!r} column.")
    available = set(data[column].dropna().astype(str))
    unknown = set(levels).difference(available)
    if unknown:
        raise ValueError(
            f"Unknown spatial_levels={sorted(unknown)}; available={sorted(available)}."
        )
    return data.loc[data[column].astype(str).isin(levels)].copy()


def _extent_frame(
    spatial: gpd.GeoDataFrame | None,
) -> gpd.GeoDataFrame | None:
    if spatial is None or spatial.empty:
        return None
    result = spatial.copy()
    result["geometry"] = result.geometry.map(polygonal_geometry)
    result = result.loc[~result.geometry.is_empty]
    return result if not result.empty else None


def map_axes(
    spatial: gpd.GeoDataFrame | None,
    *,
    figsize: tuple[float, float] = (11, 8),
    china_inset: bool | None = None,
) -> tuple[Figure, list[plt.Axes]]:
    """Create a map canvas, adding a South China Sea inset only for China."""

    figure, main_axis = plt.subplots(figsize=figsize, constrained_layout=False)
    figure.subplots_adjust(left=0.015, right=0.985, bottom=0.015, top=0.945)
    axes = [main_axis]
    use_inset = _is_national_china(spatial) and china_inset is not False
    main_axis._map_lonlat_bounds = CHINA_MAIN_BOUNDS if use_inset else None
    main_axis._map_is_inset = False
    if use_inset:
        inset_axis = main_axis.inset_axes([0.825, 0.075, 0.125, 0.225])
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
        0.025 if getattr(axis, "_map_is_inset", False) else 0.012
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


def capacity_legend_values(reference: float) -> list[float]:
    """Return the capacity examples shared by static and interactive legends."""

    return sorted({
        _nice_capacity(reference * fraction) for fraction in (0.1, 0.5, 1.0)
    })


def add_asset_legends(
    axis: plt.Axes,
    class_handles: list,
    capacity_reference: float,
) -> None:
    """Add separate class-color and total-capacity legends to an asset map."""

    class_legend = axis.legend(
        handles=class_handles,
        loc="lower left",
        bbox_to_anchor=(0.095, 0.07),
        ncol=2,
        frameon=False,
        fontsize=8,
        title="类别",
    )
    axis.add_artist(class_legend)
    values = capacity_legend_values(capacity_reference)
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
        bbox_to_anchor=(0.095, 0.88),
        frameon=False,
        fontsize=8,
        title="总容量",
        labelspacing=0.8,
    )


def class_label(value: object) -> str:
    """Return the shared Chinese display label for a standardized class."""

    return CATEGORY_LABELS_ZH.get(str(value), str(value).replace("_", " "))


def prepare_population_plot(data: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Prepare the population values used by static and web maps."""

    frame = data.copy()
    frame["_raw_value"] = pd.to_numeric(
        frame["population"], errors="coerce"
    ).fillna(0).clip(lower=0)
    frame["_value"] = np.log10(frame["_raw_value"] + 1)
    return frame


def filter_plot_extent(data: object, spatial: gpd.GeoDataFrame) -> object:
    """Limit one standard dataset to selected spatial units for plotting."""

    def spatial_union(crs: object) -> object:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="invalid value encountered in unary_union",
                category=RuntimeWarning,
            )
            return polygonal_geometry(spatial.to_crs(crs).geometry.union_all())

    if isinstance(data, gpd.GeoDataFrame):
        return data.loc[data.geometry.intersects(spatial_union(data.crs))].copy()
    if isinstance(data, xr.Dataset):
        geometry = gpd.GeoSeries.from_wkt(
            data["geometry"].values, crs=str(data.attrs["crs"])
        )
        return data.isel(
            uid=np.flatnonzero(geometry.intersects(spatial_union(geometry.crs)))
        )
    if isinstance(data, StandardNetwork):
        bus_region = spatial_union(data.bus.crs)
        branch_region = spatial_union(data.branch.crs)
        return StandardNetwork(
            data.bus.loc[data.bus.geometry.intersects(bus_region)].copy(),
            data.branch.loc[data.branch.geometry.intersects(branch_region)].copy(),
            data.transformer.loc[
                data.transformer.geometry.intersects(bus_region)
            ].copy(),
            data.converter.loc[data.converter.geometry.intersects(bus_region)].copy(),
        )
    return data


def prepare_timeseries_plot(
    data: xr.Dataset,
    *,
    variable: str,
    year: int,
    class_name: str | None,
    quantity: str,
) -> dict[str, tuple[gpd.GeoDataFrame, dict[str, object]]]:
    """Prepare annual class maps using the same aggregation for every renderer."""

    available = data["class"].values.astype(str).tolist()
    classes = [str(class_name)] if class_name else available
    unknown = set(classes).difference(available)
    if unknown:
        raise KeyError(f"Unknown class values: {sorted(unknown)}")
    geometry = _xarray_geometry(data)
    prepared = {}
    for item in classes:
        values = data[variable].sel(time=str(year), **{"class": item})
        if values.sizes.get("time", 0) == 0:
            raise ValueError(f"No {year} data are available for class={item!r}.")
        if quantity == "load":
            display = values.sum("time").compute().values / 1e6
            label = f"{year} 年用电量（TWh）"
            title = f"{class_label(item)}：{year} 年用电量"
            limits = (None, None)
            unit = "TWh"
        else:
            display = values.mean("time").compute().values
            label = f"{year} 年平均容量因子（p.u.）"
            title = f"{class_label(item)}：{year} 年平均容量因子"
            limits = (0.0, 1.0)
            unit = "p.u."
        frame = gpd.GeoDataFrame(
            {"_value": display}, geometry=geometry, crs=data.attrs["crs"]
        )
        prepared[item] = (frame, {
            "class": item,
            "label": label,
            "title": title,
            "unit": unit,
            "year": year,
            "vmin": limits[0],
            "vmax": limits[1],
        })
    return prepared


def prepare_asset_points(
    data: gpd.GeoDataFrame,
    capacity_column: str,
) -> tuple[gpd.GeoDataFrame, float, list[str]]:
    """Prepare point assets and the common capacity-size reference."""

    capacity = pd.to_numeric(data[capacity_column], errors="coerce")
    frame = data.loc[data.geometry.notna() & capacity.gt(0)].copy()
    frame["_class"] = frame["class"].astype("string").fillna("other")
    frame["_capacity"] = capacity.loc[frame.index]
    reference = max(float(frame["_capacity"].quantile(0.99)), 1.0)
    frame["_marker_size"] = 2 + 24 * np.sqrt(
        (frame["_capacity"] / reference).clip(0, 1)
    )
    classes = frame["_class"].value_counts().index.astype(str).tolist()
    return frame, reference, classes


def _single_voltage(value: object) -> float:
    if value is None or value is pd.NA:
        return np.nan
    if isinstance(value, str):
        values = value.split(",")
    else:
        try:
            values = list(value)
        except TypeError:
            values = [value]
    numeric = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
    return float(numeric.max()) if not numeric.empty else np.nan


def prepare_network_plot(
    data: StandardNetwork,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, dict[str, str]]:
    """Prepare discrete voltage/current branches and station/junction buses."""

    branches = data.branch.copy()
    branches["_voltage_kv"] = branches["voltage_kv"].map(_single_voltage)
    branches["_current_type"] = (
        branches.get("current_type", pd.Series("AC", index=branches.index))
        .astype("string").str.upper().fillna("AC")
    )
    branches["_style"] = branches.apply(
        lambda row: (
            f"{row['_voltage_kv']:g} kV {row['_current_type']}"
            if np.isfinite(row["_voltage_kv"])
            else f"Unknown kV {row['_current_type']}"
        ),
        axis=1,
    )
    voltages = sorted(branches["_voltage_kv"].dropna().unique())
    fallback = plt.colormaps["tab20"](np.linspace(0, 1, max(len(voltages), 1)))
    voltage_colors = {
        voltage: NETWORK_VOLTAGE_COLORS.get(float(voltage), fallback[index])
        for index, voltage in enumerate(voltages)
    }
    style_colors = {
        style: voltage_colors.get(float(group["_voltage_kv"].iloc[0]), "#7d8581")
        for style, group in branches.groupby("_style", observed=True)
    }
    buses = data.bus.loc[
        data.bus["subclass"].isin(["junction_bus", "station_bus"])
    ].copy()
    buses["_node_type"] = buses["subclass"].map({
        "junction_bus": "junction", "station_bus": "station",
    })
    return branches, buses, style_colors


def draw_background(
    axis: plt.Axes,
    spatial: gpd.GeoDataFrame | None,
    *,
    map_crs: str = DEFAULT_MAP_CRS,
    fill: bool = True,
    zorder: int = 0,
) -> None:
    region = province_frame(spatial)
    marine = marine_frame(spatial)
    if marine is not None:
        marine.to_crs(map_crs).plot(
            ax=axis,
            color=MARINE_COLOR if fill else "none",
            edgecolor="none",
            zorder=zorder,
        )
    if region is not None:
        region.to_crs(map_crs).plot(
            ax=axis,
            color=LAND_COLOR if fill else "none",
            edgecolor="none",
            zorder=zorder + 0.1,
        )
    extent = _extent_frame(spatial)
    if extent is not None:
        _set_axis_extent(axis, extent, map_crs)


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
            linewidth=0.42,
            alpha=0.65,
            zorder=zorder,
        )
    region = province_frame(spatial)
    if region is not None:
        region = region.to_crs(map_crs)
        region.boundary.plot(
            ax=axis,
            color=BOUNDARY_COLOR,
            linewidth=0.34,
            alpha=0.82,
            zorder=zorder,
        )
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="invalid value encountered in unary_union",
                category=RuntimeWarning,
            )
            outer_boundary = polygonal_geometry(region.geometry.union_all())
        gpd.GeoSeries([outer_boundary], crs=region.crs).boundary.plot(
            ax=axis,
            color=OUTER_BOUNDARY_COLOR,
            linewidth=0.68,
            alpha=0.92,
            zorder=zorder + 0.1,
        )


def finish_map(axis: plt.Axes, title: str = "") -> None:
    if title:
        axis.set_title(title, color="#263238", pad=2, fontsize=11)
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
    values = pd.to_numeric(frame[value_column], errors="coerce")
    lower = float(values.min()) if vmin is None else float(vmin)
    upper = float(values.max()) if vmax is None else float(vmax)
    if not np.isfinite(lower) or not np.isfinite(upper):
        lower, upper = 0.0, 1.0
    if upper <= lower:
        upper = lower + 1.0
    normalizer = Normalize(lower, upper)
    for index, axis in enumerate(axes):
        draw_background(axis, spatial, map_crs=map_crs, zorder=0)
        frame.plot(
            ax=axis,
            column=value_column,
            cmap=CONTINUOUS_CMAP,
            vmin=vmin,
            vmax=vmax,
            linewidth=0,
            edgecolor="none",
            markersize=5,
            legend=False,
            missing_kwds={"color": "#e4e6e6"},
            zorder=2,
        )
        if index == 0:
            colorbar_axis = axis.inset_axes([0.095, 0.18, 0.014, 0.22])
            colorbar = figure.colorbar(
                ScalarMappable(norm=normalizer, cmap=CONTINUOUS_CMAP),
                cax=colorbar_axis,
            )
            colorbar.set_label(label, fontsize=8, labelpad=4)
            colorbar.ax.tick_params(labelsize=7, length=2)
            colorbar.ax.yaxis.set_ticks_position("left")
            colorbar.ax.yaxis.set_label_position("left")
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

    prepared = prepare_timeseries_plot(
        data, variable=variable, year=year, class_name=class_name,
        quantity=quantity,
    )
    figures = {}
    for item, (frame, metadata) in prepared.items():
        figures[item] = continuous_map(
            frame,
            "_value",
            spatial=spatial,
            title=str(metadata["title"]),
            label=str(metadata["label"]),
            map_crs=map_crs,
            china_inset=china_inset,
            vmin=metadata["vmin"],
            vmax=metadata["vmax"],
        )
    return figures[str(class_name)] if class_name else figures


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
    other_levels = data.loc[
        ~data["level"].isin(["province", "marine_zone"])
    ].to_crs(map_crs)
    for index, axis in enumerate(axes):
        draw_background(axis, data, map_crs=map_crs, zorder=0)
        if not other_levels.empty:
            other_levels.boundary.plot(
                ax=axis,
                color="#8c6d9c",
                linewidth=0.65,
                alpha=0.9,
            )
        draw_boundaries(axis, data, map_crs=map_crs, zorder=3)
        finish_map(
            axis,
            f"Standard spatial units ({len(data):,})" if index == 0 else "",
        )
    return figure


def plot_network(
    data: StandardNetwork,
    *,
    spatial: gpd.GeoDataFrame | None = None,
    map_crs: str = DEFAULT_MAP_CRS,
    china_inset: bool | None = None,
    **_: object,
) -> Figure:
    """Plot standardized branches and junction/station buses."""

    figure, axes = map_axes(spatial, china_inset=china_inset)
    branches, buses, style_colors = prepare_network_plot(data)
    branches = branches.to_crs(map_crs)
    buses = buses.to_crs(map_crs)
    buses["geometry"] = buses.geometry.representative_point()
    bus_styles = {
        "junction": ("#596267", 0.12, 3),
        "station": ("#d1495b", 0.5, 5),
    }
    for index, axis in enumerate(axes):
        draw_background(axis, spatial, map_crs=map_crs, zorder=0)
        for style, layer in branches.groupby("_style", observed=True):
            layer.plot(
                ax=axis,
                color=style_colors[str(style)], linewidth=0.34,
                linestyle="--" if str(style).endswith(" DC") else "-",
                alpha=0.7, zorder=2,
            )
        for node_type, (color, size, order) in bus_styles.items():
            layer = buses.loc[buses["_node_type"].eq(node_type)]
            if not layer.empty:
                layer.plot(
                    ax=axis, color=color, markersize=size, marker="o",
                    alpha=0.62, zorder=order,
                )
        if index == 0:
            branch_handles = [
                Line2D(
                    [0], [0], color=style_colors[str(style)],
                    linestyle="--" if str(style).endswith(" DC") else "-",
                    linewidth=1.2, label=str(style),
                )
                for style in sorted(style_colors, key=network_style_sort_key)
            ]
            axis.legend(
                handles=[*branch_handles,
                    Line2D([0], [0], marker="o", linestyle="none",
                           color="#596267", markersize=4,
                           label="Junction"),
                    Line2D([0], [0], marker="o", linestyle="none",
                           color="#d1495b", markersize=5,
                           label="Station"),
                ],
                loc="lower left",
                bbox_to_anchor=(0.095, 0.015),
                ncol=2, frameon=False, fontsize=7,
            )
        finish_map(
            axis,
            (
                f"Standard network: {len(data.bus):,} buses, "
                f"{len(data.branch):,} branches"
                if index == 0 else ""
            ),
        )
    return figure


def network_style_sort_key(style: object) -> tuple[float, str]:
    text = str(style)
    try:
        voltage = float(text.split(" ", 1)[0])
    except ValueError:
        voltage = np.inf
    return voltage, text


def asset_point_map(
    data: gpd.GeoDataFrame,
    capacity_column: str,
    title: str,
    spatial: gpd.GeoDataFrame | None,
    map_crs: str,
    china_inset: bool | None,
) -> Figure:
    """Plot point assets with class-specific colors and markers."""

    frame, reference, classes = prepare_asset_points(data, capacity_column)
    frame = frame.to_crs(map_crs)
    figure, axes = map_axes(spatial, china_inset=china_inset)
    fallback = plt.colormaps["tab10"](np.linspace(0, 1, len(classes)))
    handles = []
    for class_index, item in enumerate(classes):
        color = CATEGORY_COLORS.get(item, fallback[class_index])
        marker = CATEGORY_MARKERS.get(item, "o")
        handles.append(Line2D(
            [0], [0], marker=marker, linestyle="none", markerfacecolor=color,
            markeredgecolor="white", markersize=7, label=class_label(item),
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


def plot_generator(
    data: gpd.GeoDataFrame,
    *,
    spatial: gpd.GeoDataFrame | None = None,
    map_crs: str = DEFAULT_MAP_CRS,
    china_inset: bool | None = None,
    **_: object,
) -> Figure:
    return asset_point_map(
        data, "capacity_mw", "Generator assets by class", spatial, map_crs,
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
    frame = prepare_population_plot(data)
    return continuous_map(
        frame,
        "_value",
        spatial=spatial,
        title="Population by standardized source cell",
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


def plot_parameter(data: pd.DataFrame, **_: object) -> Figure:
    """Plot parameter coverage by standardized asset class."""

    frame = data.loc[
        data["class"].notna() & data["name"].notna(),
        ["class", "name"],
    ]
    coverage = pd.crosstab(frame["class"], frame["name"])
    figure, axis = plt.subplots(figsize=(12, 6.5), constrained_layout=True)
    image = axis.imshow(coverage, cmap=CONTINUOUS_CMAP, aspect="auto")
    axis.set_xticks(range(len(coverage.columns)), coverage.columns, rotation=60)
    axis.set_yticks(range(len(coverage.index)), coverage.index)
    axis.set_title("Parameter coverage")
    figure.colorbar(image, ax=axis, label="Parameter records")
    return figure


PLOTTERS: dict[str, Callable[..., PlotResult]] = {
    "spatial": plot_spatial,
    "network": plot_network,
    "generator": plot_generator,
    "storage": plot_storage,
    "parameter": plot_parameter,
    "load": plot_load,
    "population": plot_population,
    "resource": plot_resource,
}
