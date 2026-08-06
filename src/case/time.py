"""Case time-window and resolution handling."""

from __future__ import annotations

import pandas as pd
import xarray as xr

from ..mapping.time import align_time


def select_time(data: xr.Dataset, options: dict) -> xr.Dataset:
    """Align timezone/resolution and select an inclusive case time window."""

    result = align_time(
        data,
        timezone=str(options["timezone"]),
        time_step=str(options["time_step"]),
    )
    start = _naive_local(options.get("start"), str(options["timezone"]))
    end = _naive_local(options.get("end"), str(options["timezone"]))
    return result.sel(time=slice(start, end))


def common_time(load: xr.Dataset, resource: xr.Dataset) -> tuple[xr.Dataset, xr.Dataset]:
    """Restrict time-series inputs to their common timestamps."""

    index = pd.DatetimeIndex(load.time.values).intersection(resource.time.values)
    if index.empty:
        raise ValueError("Load and resource have no common case timestamps.")
    return load.sel(time=index), resource.sel(time=index)


def _naive_local(value: object, timezone: str) -> pd.Timestamp | None:
    if value in (None, ""):
        return None
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert(timezone).tz_localize(None)
    return timestamp
