"""Persistent UC result data independent of plotting."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from ...case import PowerSystemCase
from .model import UnitCommitmentResult


def operation_dataset(
    network,
    snapshots: pd.DatetimeIndex,
    *,
    timezone: str,
) -> xr.Dataset:
    """Extract component-level optimization time series from PyPSA."""

    data = xr.Dataset(
        coords={"time": snapshots.to_numpy()},
        attrs={"timezone": timezone, "time_step": _time_step(snapshots)},
    )
    specs = (
        ("generator", network.generators, network.generators_t, (("p", "p_mw", "MW"),)),
        ("storage", network.storage_units, network.storage_units_t, (
            ("p", "p_mw", "MW"),
            ("state_of_charge", "state_of_charge_mwh", "MWh"),
        )),
        ("load", network.loads, network.loads_t, (("p_set", "p_set_mw", "MW"),)),
        ("line", network.lines, network.lines_t, (("p0", "p0_mw", "MW"), ("p1", "p1_mw", "MW"))),
        ("transformer", network.transformers, network.transformers_t, (("p0", "p0_mw", "MW"), ("p1", "p1_mw", "MW"))),
        ("link", network.links, network.links_t, (("p0", "p0_mw", "MW"), ("p1", "p1_mw", "MW"))),
    )
    for component, static, dynamic, variables in specs:
        dimension = f"{component}_uid"
        uids = static.index.astype(str).to_numpy()
        data = data.assign_coords({dimension: uids})
        for source_name, output_name, unit in variables:
            frame = getattr(dynamic, source_name).reindex(
                index=snapshots, columns=static.index
            )
            data[f"{component}_{output_name}"] = (
                ("time", dimension), frame.to_numpy(dtype=float)
            )
            data[f"{component}_{output_name}"].attrs["unit"] = unit
        for column in ("bus", "bus0", "bus1", "carrier"):
            if column in static:
                data = data.assign_coords({
                    f"{component}_{column}": (
                        dimension, static[column].astype(str).to_numpy()
                    )
                })
    return data


def save_result(result: UnitCommitmentResult, root: Path) -> None:
    """Write raw operation time series and scalar solve summary."""

    root.mkdir(parents=True, exist_ok=True)
    path = root / "timeseries.nc"
    temporary = path.with_suffix(".nc.tmp")
    encoding = {
        name: {"zlib": True, "complevel": 4}
        for name in result.data.data_vars
    }
    result.data.to_netcdf(temporary, encoding=encoding)
    temporary.replace(path)
    (root / "summary.json").write_text(
        json.dumps(
            {str(key): _json_value(value) for key, value in result.summary.items()},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def load_result(case: PowerSystemCase, root: Path) -> UnitCommitmentResult:
    """Load a saved operation result without re-running optimization."""

    data = xr.open_dataset(root / "timeseries.nc", chunks="auto")
    summary = pd.Series(
        json.loads((root / "summary.json").read_text(encoding="utf-8")),
        name="value",
    )
    return UnitCommitmentResult(
        case=case,
        network=None,
        data=data,
        status=str(summary["solver_status"]),
        condition=str(summary["termination_condition"]),
        snapshots=pd.DatetimeIndex(data.time.values),
        summary=summary,
    )


def _json_value(value: object) -> object:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _time_step(snapshots: pd.DatetimeIndex) -> str:
    if len(snapshots) >= 3:
        return pd.infer_freq(snapshots) or "irregular"
    if len(snapshots) == 2:
        return str(snapshots[1] - snapshots[0])
    return "snapshot"
