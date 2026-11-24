"""Local GRIB preparation and multi-year ERA5 resource contracts."""

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from src.raw.era5 import prepare_era5_fields
from src.standard.resource import _merge_era5_sources
from src.standard.resource import _ResourceStandardizer
from src.standard.schema import _validate_dataset
from pathlib import Path
from types import SimpleNamespace
import tomllib


def _fields(start="2025-01-01"):
    values = dict(u10=3, v10=4, u100=6, v100=8, fsr=.1, ssr=288000,
                  ssrd=360000, tisr=720000, fdir=180000, t2m=290,
                  d2m=280, stl4=285, ro=.001)
    return xr.Dataset({k: (("valid_time", "latitude", "longitude"),
                           np.full((2, 2, 2), v, dtype="float32")) for k, v in values.items()},
                      coords=dict(valid_time=pd.date_range(start, periods=2, freq="1h"),
                                  latitude=[18.25, 18], longitude=[73, 73.25])).chunk({"valid_time": 1})


def test_local_conversion_matches_atlite_feature_calculations(monkeypatch):
    from atlite.datasets import era5

    data = _fields()
    for name in ("u10", "v10", "u100", "v100"):
        data[name].attrs["units"] = "m s**-1"
    actual = prepare_era5_fields(data)
    monkeypatch.setattr(era5, "retrieve_data", lambda **kwargs: data)
    for feature in ("wind", "influx", "temperature", "runoff"):
        expected = getattr(era5, f"get_data_{feature}")({})
        sanitizer = getattr(era5, f"sanitize_{feature}", lambda d: d)
        expected = sanitizer(expected)[era5.features[feature]]
        xr.testing.assert_allclose(actual[era5.features[feature]], expected)
    assert actual.influx_direct.isel(time=0, y=0, x=0).compute().item() == pytest.approx(50)


def test_missing_weather_variables_are_reported():
    with pytest.raises(ValueError, match="fsr"):
        prepare_era5_fields(_fields().drop_vars("fsr"))


def test_years_share_one_grid_and_keep_source_by_timestamp():
    old = prepare_era5_fields(_fields("2024-12-31T22:00"))
    new = prepare_era5_fields(_fields())
    merged = _merge_era5_sources([new, old], ["2025", "2024"])
    assert merged.sizes == {"time": 4, "y": 2, "x": 2}
    assert merged.weather_source_id.values.tolist() == ["2024", "2024", "2025", "2025"]
    with pytest.raises(ValueError, match="overlap"):
        _merge_era5_sources([old, old], ["first", "second"])
    with pytest.raises(ValueError, match="different ERA5 grid"):
        _merge_era5_sources([old, new.assign_coords(x=[74, 74.25])], ["first", "second"])


def test_resource_build_combines_years_without_duplicate_spatial_uids(tmp_path):
    import atlite

    paths = {}
    for source, start in [("2024", "2024-12-31T22:00"), ("2025", "2025-01-01")]:
        paths[source] = tmp_path / f"{source}.nc"
        prepare_era5_fields(_fields(start).assign_coords(longitude=[115, 115.25])).to_netcdf(paths[source])
    config = tomllib.loads((Path(__file__).resolve().parents[1] / "config/standard_data.toml").read_text())
    config = config["datasets"]["resource"]
    config["source_ids"] = list(paths)
    config["options"]["run_of_river_smoothing_hours"] = 2
    manager = SimpleNamespace(datasets={"resource": config}, output_root=tmp_path,
                              raw_data=SimpleNamespace(get_file=lambda source: paths[source]))
    _ResourceStandardizer(manager, "resource").build()
    with xr.open_dataset(tmp_path / config["output"]) as result:
        _validate_dataset(result, "resource")
        assert result.sizes == {"time": 4, "uid": 4, "class": 4}
        assert len(set(result.uid.values)) == 4
        assert result.weather_source_id.values.tolist() == ["2024", "2024", "2025", "2025"]
        assert bool(np.isfinite(result.availability_pu).all())
        assert float(result.availability_pu.min()) >= 0
        assert float(result.availability_pu.max()) <= 1
        reference = atlite.Cutout(paths["2025"])
        try:
            expected_wind = reference.wind(turbine=config["options"]["onshore_turbine"],
                                           add_cutout_windspeed=True, capacity_factor_timeseries=True)
            expected_pv = reference.pv(panel="CSi", orientation="latitude_optimal",
                                      clearsky_model="enhanced", capacity_factor_timeseries=True)
            np.testing.assert_allclose(result.availability_pu.sel({"class": "onshore"}).values[2:],
                                       expected_wind.transpose("time", "y", "x").values.reshape(2, 4), rtol=1e-5)
            np.testing.assert_allclose(result.availability_pu.sel({"class": "utility_scale_pv"}).values[2:],
                                       expected_pv.transpose("time", "y", "x").values.reshape(2, 4), rtol=1e-5)
            assert float(expected_pv.max()) > 0
        finally:
            reference.data.close()
