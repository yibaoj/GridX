"""Import downloaded hourly CDS GRIB into the existing atlite cutout format."""

from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
from ..common.io import NETCDF_ENGINE


def prepare_era5_fields(data: xr.Dataset) -> xr.Dataset:
    """Process hourly ERA5 single levels using atlite 0.4 conventions."""

    from atlite.datasets import era5
    from atlite.pv.solar_position import SolarPosition

    required = {"u10", "v10", "u100", "v100", "fsr", "ssr", "ssrd", "tisr",
                "fdir", "t2m", "d2m", "stl4", "ro"}
    if missing := required.difference(data.data_vars):
        raise ValueError(f"ERA5 GRIB is missing required variables: {sorted(missing)}")
    data = era5._rename_and_clean_coords(data)
    speed10 = np.hypot(data.u10, data.v10)
    speed100 = np.hypot(data.u100, data.v100)
    azimuth = np.arctan2(data.u100, data.v100)
    result = xr.Dataset({
        "wnd100m": speed100,
        "wnd_shear_exp": np.log(speed10 / speed100) / np.log(10 / 100),
        "wnd_azimuth": azimuth.where(azimuth >= 0, azimuth + 2 * np.pi),
        "roughness": data.fsr,
        "influx_direct": data.fdir / 3600,
        "influx_diffuse": (data.ssrd - data.fdir) / 3600,
        "influx_toa": data.tisr / 3600,
        "albedo": ((data.ssrd - data.ssr) / data.ssrd.where(data.ssrd != 0)).fillna(0),
        "temperature": data.t2m,
        "soil temperature": data.stl4,
        "dewpoint temperature": data.d2m,
        "runoff": data.ro,
    })
    solar = SolarPosition(result, time_shift="-30min")
    result = xr.merge([result, solar.rename({v: f"solar_{v}" for v in solar.data_vars})])
    result = era5.sanitize_runoff(era5.sanitize_influx(era5.sanitize_wind(result)))
    units = {"wnd100m": "m s**-1", "wnd_shear_exp": "1", "wnd_azimuth": "rad",
             "roughness": "m", "albedo": "1", "temperature": "K", "soil temperature": "K",
             "dewpoint temperature": "K", "runoff": "m", "solar_altitude": "rad", "solar_azimuth": "rad"}
    for feature, variables in era5.features.items():
        for name in variables:
            if name in result:
                result[name].attrs.update(module="era5", feature=feature)
                if name.startswith("influx_"):
                    result[name].attrs["units"] = "W m**-2"
                else:
                    result[name].attrs["units"] = units[name]
    result.attrs = dict(module="era5", prepared_features=["wind", "influx", "temperature", "runoff"])
    return result.astype("float32")


def convert_era5_grib(grib_path, nc_path, *, cache_dir="outputs/cache/era5", overwrite=False) -> Path:
    """Convert hourly reanalysis GRIB, preserving the original file unchanged.

    Radiation must be one-hour accumulated energy, as in CDS ERA5 hourly
    single-level downloads, not ERA5-Land or cumulative forecast products.
    """

    import dask

    grib_path, nc_path = Path(grib_path).resolve(), Path(nc_path).resolve()
    if nc_path.exists() and not overwrite:
        raise FileExistsError(nc_path)
    cache_dir = Path(cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    nc_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = f"{grib_path.stem}.{grib_path.stat().st_size}.{grib_path.stat().st_mtime_ns}"
    temporary = nc_path.with_suffix(".part.nc")
    try:
        with xr.open_dataset(
            grib_path, engine="cfgrib", chunks={"valid_time": 24},
            backend_kwargs=dict(
                time_dims=["valid_time"], ignore_keys=["edition"],
                coords_as_attributes=["surface", "depthBelowLandLayer", "entireAtmosphere",
                                      "heightAboveGround", "meanSea"],
                indexpath=str(cache_dir / f"{stamp}.{{short_hash}}.idx"),
                read_keys=["startStep", "endStep", "stepUnits"],
            ),
        ) as data:
            for name in ("ssr", "ssrd", "tisr", "fdir", "ro"):
                if name in data:
                    attrs = data[name].attrs
                    if (attrs.get("GRIB_stepUnits") != 1
                        or attrs.get("GRIB_endStep", 0) - attrs.get("GRIB_startStep", 0) != 1):
                        raise ValueError(f"{name} must contain one-hour accumulated fields.")
            result = prepare_era5_fields(data)
            index = pd.DatetimeIndex(result.time.values)
            if index.empty or not index.equals(pd.date_range(index[0], index[-1], freq="1h")):
                raise ValueError("ERA5 input must have a complete, increasing hourly time axis.")
            result.attrs.update(source_file=grib_path.name, timezone="UTC", time_step="1h")
            encoding = {
                name: dict(zlib=True, complevel=4,
                           chunksizes=tuple(chunks[0] for chunks in result[name].chunks))
                for name in result.data_vars
            }
            with dask.config.set(scheduler="synchronous"):
                result.to_netcdf(temporary, engine=NETCDF_ENGINE, encoding=encoding)
        temporary.replace(nc_path)
    finally:
        temporary.unlink(missing_ok=True)
    return nc_path
