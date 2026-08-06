"""UC application result object."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ...case import PowerSystemCase


@dataclass(frozen=True)
class UnitCommitmentResult:
    """Solved PyPSA network and compact operation summary."""

    case: PowerSystemCase
    network: object
    status: str
    condition: str
    snapshots: pd.DatetimeIndex
    summary: pd.Series

    def plot(self, **kwargs):
        """Return a colored stacked production-simulation figure."""

        from .plot import plot_dispatch

        return plot_dispatch(self, **kwargs)
