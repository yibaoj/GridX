"""UC application result object."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import xarray as xr

from ...case import PowerSystemCase


@dataclass(frozen=True)
class UnitCommitmentResult:
    """Solved PyPSA network and compact operation summary."""

    case: PowerSystemCase
    network: object
    data: xr.Dataset
    status: str
    condition: str
    snapshots: pd.DatetimeIndex
    summary: pd.Series

    def plot(self, **kwargs):
        """Return a figure from saved operation data without writing a file."""

        from .plot import plot_dispatch

        return plot_dispatch(self, **kwargs)
