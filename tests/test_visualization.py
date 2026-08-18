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
