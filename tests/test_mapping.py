"""Focused regression tests for spatial mapping helpers."""

import geopandas as gpd
from shapely.geometry import box
import numpy as np
import pandas as pd
import xarray as xr

from src.mapping.space import map_timeseries_to_cells


def test_extensive_mapping_sets_uncovered_zero_population_cell_to_zero() -> None:
    source = xr.Dataset(
        {"demand_mw": (("time", "uid", "class"), [[[100.0]]])},
        coords={
            "time": pd.date_range("2024-01-01", periods=1, freq="1h"),
            "uid": ["province:land"],
            "class": ["electric_load"],
            "geometry": ("uid", [box(0, 0, 2, 1).wkt]),
        },
        attrs={"standard_dataset_id": "load", "crs": "EPSG:3857"},
    )
    cells = gpd.GeoDataFrame(
        {
            "spatial_uid": [
                "cell:land", "cell:marine", "cell:outside-zero",
                "cell:outside-populated",
            ],
            "spatial_level": [
                "province", "marine_zone", "marine_zone", "province",
            ],
            "admin_uid": [
                "province:land", "marine:zone", "marine:zone", "province:missing",
            ],
        },
        geometry=[
            box(0, 0, 1, 1), box(1, 0, 2, 1), box(2, 0, 3, 1),
            box(3, 0, 4, 1),
        ],
        crs="EPSG:3857",
    )
    population = cells.copy()
    population["population"] = [10.0, 0.0, 0.0, 10.0]

    result = map_timeseries_to_cells(
        source,
        cells,
        variable="demand_mw",
        quantity_kind="extensive",
        method="auxiliary",
        metric_crs="EPSG:3857",
        auxiliary_cells=population,
        auxiliary_value="population",
    ).compute()

    values = result["demand_mw"].isel(time=0, **{"class": 0}).values
    np.testing.assert_allclose(values[:3], [100.0, 0.0, 0.0])
    assert np.isnan(values[3])


def test_positive_nearshore_auxiliary_cell_uses_nearest_source_region() -> None:
    source = xr.Dataset(
        {"demand_mw": (("time", "uid", "class"), [[[100.0]]])},
        coords={
            "time": pd.date_range("2024-01-01", periods=1, freq="1h"),
            "uid": ["province:land"],
            "class": ["electric_load"],
            "geometry": ("uid", [box(0, 0, 1, 1).wkt]),
        },
        attrs={"standard_dataset_id": "load", "crs": "EPSG:3857"},
    )
    cells = gpd.GeoDataFrame(
        {
            "spatial_uid": ["cell:land", "cell:nearshore"],
            "spatial_level": ["province", "marine_zone"],
            "admin_uid": ["province:land", "marine:zone"],
        },
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)],
        crs="EPSG:3857",
    )
    population = cells.copy()
    population["population"] = [10.0, 10.0]

    result = map_timeseries_to_cells(
        source,
        cells,
        variable="demand_mw",
        quantity_kind="extensive",
        method="auxiliary",
        metric_crs="EPSG:3857",
        auxiliary_cells=population,
        auxiliary_value="population",
        uncovered_auxiliary_nearest_levels=["marine_zone"],
    ).compute()

    np.testing.assert_allclose(
        result["demand_mw"].isel(time=0, **{"class": 0}), [50.0, 50.0]
    )
