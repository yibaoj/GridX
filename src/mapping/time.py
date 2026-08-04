"""Time-coordinate alignment."""

from __future__ import annotations

import pandas as pd
import xarray as xr


def align_time(data: xr.Dataset, *, timezone: str, time_step: str) -> xr.Dataset:
    """Convert timestamps to one timezone and temporal resolution."""

    source_timezone = str(data.attrs["timezone"])
    index = pd.DatetimeIndex(data["time"].values)
    if index.tz is None:
        index = index.tz_localize(source_timezone)
    index = index.tz_convert(timezone).tz_localize(None)
    result = data.assign_coords(time=index.to_numpy())
    source_step = pd.Timedelta(str(data.attrs["time_step"]))
    target_step = pd.Timedelta(time_step)
    if target_step > source_step:
        result = result.resample(time=time_step).mean()
    elif target_step < source_step:
        result = result.resample(time=time_step).interpolate("linear")
    return result.assign_attrs({
        **data.attrs,
        "timezone": timezone,
        "time_step": time_step,
    })
