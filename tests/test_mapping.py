"""Focused regression tests for spatial mapping helpers."""

import geopandas as gpd
from shapely.geometry import box
import numpy as np
import pandas as pd
import xarray as xr
import dask.array as da

from src.mapping.space import map_timeseries_to_cells
from src.mapping.manager import SpatiotemporalMappingManager
from src.standard.schema import TIME_CHUNK_SIZE
from src.standard.schema import _write_xarray
from src.standard import StandardDataManager


def test_standard_load_respects_existing_storage_chunks_without_warning(tmp_path):
    import warnings

    data = xr.Dataset({"demand_mw": (("time", "uid", "class"), np.ones((250, 2, 1), dtype="float32"))},
                      coords={"time": pd.date_range("2024-01-01", periods=250, freq="1h"),
                              "uid": ["a", "b"], "class": ["electric_load"],
                              "location": ("uid", ["a", "b"]),
                              "geometry": ("uid", ["POINT (0 0)", "POINT (1 0)"]),
                              "geometry_method": ("uid", ["source"] * 2)},
                      attrs=dict(standard_dataset_id="load", crs="EPSG:4326", timezone="UTC",
                                 time_step="1h", source_unit="MW", unit="MW", source_id="test"))
    path = tmp_path / "load.nc"
    data.to_netcdf(path, encoding={"demand_mw": {"chunksizes": (250, 2, 1)}})
    manager = object.__new__(StandardDataManager)
    manager.datasets = {"load": {"output": "load.nc"}}
    manager.output_root = tmp_path
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        loaded = manager.load("load")
    try:
        assert not any("separate the stored chunks" in str(w.message) for w in caught)
        assert loaded.demand_mw.chunks[0] == (250,)
        np.testing.assert_allclose(loaded.demand_mw, data.demand_mw)
    finally:
        loaded.close()
    new_path = tmp_path / "new.nc"
    _write_xarray(data, new_path, "demand_mw")
    with xr.open_dataset(new_path, chunks={}) as result:
        assert max(result.demand_mw.chunks[0]) <= TIME_CHUNK_SIZE
        np.testing.assert_allclose(result.demand_mw, data.demand_mw)


def test_spatial_expansion_bounds_time_blocks_and_preserves_values():
    geometry = [box(0, 0, 1, 1), box(1, 0, 2, 1)]
    data = xr.Dataset(
        {"availability_pu": (("time", "uid", "class"), da.ones((250, 2, 2), chunks=(250, 1, 1)))},
        coords={"time": pd.date_range("2024-01-01", periods=250, freq="1h"),
                "uid": ["a", "b"], "class": ["wind", "pv"],
                "geometry": ("uid", [g.wkt for g in geometry]),
                "weather_source_id": ("time", ["era5"] * 250)},
        attrs={"standard_dataset_id": "resource", "crs": "EPSG:3857"},
    )
    cells = gpd.GeoDataFrame(dict(spatial_uid=["a", "b"], spatial_level=["province"] * 2,
                                  admin_uid=["province:a", "province:b"]), geometry=geometry, crs=3857)
    result = map_timeseries_to_cells(data, cells, variable="availability_pu", quantity_kind="intensive",
                                     method="linear", metric_crs="EPSG:3857")
    assert max(result.availability_pu.chunks[0]) <= TIME_CHUNK_SIZE
    np.testing.assert_allclose(result.availability_pu.compute(), 1)
    assert result.weather_source_id.values.tolist() == ["era5"] * 250


def test_mapping_netcdf_write_uses_main_thread_and_matching_chunks(tmp_path):
    import threading
    from dask import delayed

    main_thread = threading.get_ident()
    def read_block():
        assert threading.get_ident() == main_thread
        return np.ones((3, 2, 1), dtype="float32")
    values = da.from_delayed(delayed(read_block)(), shape=(3, 2, 1), dtype="float32")
    data = xr.Dataset({"value": (("time", "uid", "class"), values)},
                      coords={"time": pd.date_range("2024-01-01", periods=3, freq="1h"),
                              "uid": ["a", "b"], "class": ["test"]})
    manager = object.__new__(SpatiotemporalMappingManager)
    manager.outputs = {"test": tmp_path / "result.nc"}
    manager._write_xarray("test", data, "value")
    with xr.open_dataset(manager.outputs["test"]) as result:
        np.testing.assert_allclose(result.value, 1)
        assert result.value.encoding["chunksizes"] == (3, 2, 1)


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
