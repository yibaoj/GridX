"""Tests for shared plotting configuration and language behavior."""

import geopandas as gpd
import matplotlib.pyplot as plt
from shapely.geometry import box

from src.standard.plot import map_axes
from src.visualization import resolve_plot_settings
from src.visualization.labels import class_label, text


def test_plot_settings_allow_language_and_inset_overrides() -> None:
    defaults = resolve_plot_settings()
    english = resolve_plot_settings(language="en", china_inset=False)

    assert defaults.language == "zh"
    assert defaults.china_inset is True
    assert english.language == "en"
    assert english.china_inset is False


def test_plot_labels_are_bilingual() -> None:
    assert class_label("wind", "zh") == "风电"
    assert class_label("wind", "en") == "Wind"
    assert text("total_capacity", "zh") == "总容量"
    assert text("total_capacity", "en") == "Total capacity"


def test_complete_china_adds_inset_but_other_regions_do_not() -> None:
    adcodes = ["110000", "310000", "460000", "650000"] + [
        f"{index:06d}" for index in range(26)
    ]
    china = gpd.GeoDataFrame(
        {"level": "province", "adcode": adcodes},
        geometry=[box(73, 18, 135, 54)] * len(adcodes),
        crs="EPSG:4326",
    )
    region = gpd.GeoDataFrame(
        {"level": ["province"], "adcode": ["000001"]},
        geometry=[box(100, 20, 110, 30)],
        crs="EPSG:4326",
    )

    china_figure, china_axes = map_axes(china, china_inset=True)
    case_background = china.drop(columns="adcode").assign(
        uid=[f"province_boundaries:{value}" for value in adcodes]
    )
    case_figure, case_axes = map_axes(case_background, china_inset=True)
    region_figure, region_axes = map_axes(region, china_inset=True)
    assert len(china_axes) == 2
    assert len(case_axes) == 2
    assert len(region_axes) == 1
    plt.close(china_figure)
    plt.close(case_figure)
    plt.close(region_figure)


def test_network_voltage_filter_preserves_inputs_and_handles_empty_result():
    from types import SimpleNamespace
    from shapely.geometry import Point, LineString
    from src.standard.plot import prepare_network_plot

    buses = gpd.GeoDataFrame(
        {"uid": ["a", "b", "c"], "voltage_kv": [500., 500., 220.],
         "subclass": ["station_bus"] * 3},
        geometry=[Point(0, 0), Point(1, 0), Point(2, 0)], crs="EPSG:4326",
    )
    branches = gpd.GeoDataFrame(
        {"uid": ["ab", "bc"], "voltage_kv": [500., 220.],
         "from_bus_uid": ["a", "b"], "to_bus_uid": ["b", "c"]},
        geometry=[LineString([(0, 0), (1, 0)]), LineString([(1, 0), (2, 0)])],
        crs="EPSG:4326",
    )
    network = SimpleNamespace(bus=buses, branch=branches)
    lines, nodes, colors = prepare_network_plot(network, minimum_voltage_kv=500)
    assert lines.uid.tolist() == ["ab"]
    assert nodes.uid.tolist() == ["a", "b"]
    assert set(colors) == {"500 kV AC"}
    assert len(branches) == 2 and len(buses) == 3
    lines, nodes, colors = prepare_network_plot(network, minimum_voltage_kv=1000)
    assert lines.empty and nodes.empty and not colors
