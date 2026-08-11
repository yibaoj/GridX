"""PyPSA production simulation built only from PowerSystemCase."""

from __future__ import annotations

from pathlib import Path
import tomllib
import warnings

import numpy as np
import pandas as pd

from ...case import PowerSystemCase
from .formulation import formulation_table
from .io import load_result, operation_dataset, save_result
from .model import UnitCommitmentResult


class UnitCommitmentApplication:
    """Build and solve a configured PyPSA UC/ED application."""

    def __init__(
        self,
        case: PowerSystemCase,
        config_path: str | Path = "config/uc.toml",
    ) -> None:
        path = Path(config_path).expanduser()
        if not path.is_absolute() and not path.exists():
            path = Path(__file__).resolve().parents[3] / path
        with path.resolve().open("rb") as file:
            self.config = tomllib.load(file)
        self.output_root = (
            path.resolve().parents[1] / self.config["general"]["output_root"]
        )
        self.case = case
        self._last_model = None

    def list(
        self,
        sections=None,
        *,
        active_only: bool = False,
    ) -> pd.DataFrame:
        """List the configured formulation and realized PyPSA model symbols."""

        return formulation_table(
            self.case,
            self.config,
            model=self._last_model,
            sections=sections,
            active_only=active_only,
        )

    def run(
        self,
        *,
        start: object = None,
        end: object = None,
    ) -> UnitCommitmentResult:
        """Solve the requested snapshot range and return standardized results."""

        options = self.config["general"]
        network = self.case.to_pypsa(strict=bool(options["strict_case"]))
        start = options.get("default_start") if start is None else start
        end = options.get("default_end") if end is None else end
        snapshots = _snapshots(network.snapshots, start, end)
        if snapshots.empty:
            raise ValueError("The selected UC time range contains no snapshots.")
        if str(options["commitment_mode"]) != "continuous":
            raise NotImplementedError(
                "Only continuous clustered dispatch is currently validated."
            )
        _add_load_shedding(network, snapshots, self.config["reliability"])
        solver = self.config["solver"]
        solver_options = {
            "time_limit": float(solver["time_limit_s"]),
            "mip_rel_gap": float(solver["mip_gap"]),
        }
        if solver.get("method"):
            solver_options["solver"] = str(solver["method"])
        status, condition, objective = _optimize(
            network,
            snapshots,
            solve_mode=str(options.get("solve_mode", "single")),
            horizon=int(options.get("rolling_horizon_hours", 24)),
            overlap=int(options.get("rolling_overlap_hours", 0)),
            solver_name=str(solver["name"]),
            solver_options=solver_options,
            log_to_console=bool(solver["log_to_console"]),
        )
        condition_name = str(condition).lower()
        has_solution = objective is not None and np.isfinite(float(objective))
        if str(status).lower() != "ok" or not has_solution:
            raise RuntimeError(f"UC optimization failed: {status}, {condition}")
        if condition_name not in {"optimal", "feasible"}:
            warnings.warn(
                f"UC returned a feasible solution with {condition_name!r}; "
                "optimality was not established.",
                RuntimeWarning,
                stacklevel=2,
            )
        self._last_model = network.model
        load = network.loads_t.p_set.reindex(snapshots).sum(axis=1)
        shedding = _generator_output(
            network, snapshots, carrier="load_shedding"
        ).sum(axis=1)
        storage = network.storage_units_t.p.reindex(snapshots)
        weights = network.snapshot_weightings["generators"].reindex(snapshots)
        summary = pd.Series({
            "solver_status": status,
            "termination_condition": condition,
            "is_optimal": condition_name == "optimal",
            "objective": objective,
            "snapshots": len(snapshots),
            "load_mwh": load.mul(weights).sum(),
            "load_shedding_mwh": shedding.mul(weights).sum(),
            "load_shedding_share": (
                shedding.mul(weights).sum() / load.mul(weights).sum()
                if load.mul(weights).sum() else 0
            ),
            "storage_discharge_mwh": storage.clip(lower=0).mul(
                weights, axis=0
            ).sum().sum(),
            "storage_charge_mwh": -storage.clip(upper=0).mul(
                weights, axis=0
            ).sum().sum(),
            "case_minimum_voltage_kv": float(
                self.case.config["network"]["minimum_voltage_kv"]
            ),
            "solve_mode": str(options.get("solve_mode", "single")),
            "rolling_horizon_hours": int(
                options.get("rolling_horizon_hours", len(snapshots))
            ),
        }, name="value")
        diagnostics = _validate_dispatch(
            network,
            snapshots,
            tolerance=float(options["dispatch_tolerance_mw"]),
        )
        summary = pd.concat([summary, pd.Series(diagnostics)])
        if not bool(diagnostics["dispatch_valid"]):
            raise RuntimeError(
                "UC returned a numerically invalid dispatch; outputs were not saved. "
                f"Diagnostics: {diagnostics}"
            )
        result = UnitCommitmentResult(
            case=self.case,
            network=network,
            data=operation_dataset(
                network,
                snapshots,
                timezone=str(self.case.config["time"]["timezone"]),
            ),
            status=str(status),
            condition=str(condition),
            snapshots=snapshots,
            summary=summary,
        )
        save_result(result, self.output_root)
        return result

    def load(self) -> UnitCommitmentResult:
        """Load the latest saved UC result without solving again."""

        missing = [
            path for path in (
                self.output_root / "timeseries.nc",
                self.output_root / "summary.json",
            )
            if not path.exists()
        ]
        if missing:
            raise FileNotFoundError(
                f"UC outputs are unavailable; run run() first. Missing: {missing}"
            )
        return load_result(self.case, self.output_root)

    def plot(self, **kwargs):
        """Load saved UC data and return a plot without saving an image."""

        return self.load().plot(**kwargs)


def _snapshots(
    snapshots: pd.Index,
    start: object,
    end: object,
) -> pd.DatetimeIndex:
    index = pd.DatetimeIndex(snapshots)
    start = index.min() if start is None else pd.Timestamp(start)
    end = index.max() if end is None else pd.Timestamp(end)
    return index[(index >= start) & (index <= end)]


def _optimize(
    network,
    snapshots: pd.DatetimeIndex,
    *,
    solve_mode: str,
    horizon: int,
    overlap: int,
    **kwargs,
) -> tuple[str, str, float | None]:
    """Solve once or in chronological windows while preserving storage state."""

    optimize_options = {
        **kwargs,
        "include_objective_constant": False,
    }
    if solve_mode == "single":
        status, condition = network.optimize(
            snapshots=snapshots, **optimize_options
        )
        return str(status), str(condition), network.objective
    if solve_mode != "rolling_horizon":
        raise ValueError("solve_mode must be 'single' or 'rolling_horizon'.")
    if horizon <= overlap or overlap != 0:
        raise ValueError(
            "Rolling UC currently requires horizon > 0 and overlap = 0 so "
            "objective and saved dispatch are counted exactly once."
        )

    objectives = []
    conditions = []
    statuses = []
    for window_index, start in enumerate(range(0, len(snapshots), horizon)):
        window = snapshots[start:min(start + horizon, len(snapshots))]
        if window_index and len(network.storage_units.index):
            previous = snapshots[start - 1]
            network.storage_units["state_of_charge_initial"] = (
                network.storage_units_t.state_of_charge.loc[previous]
                .reindex(network.storage_units.index)
                .fillna(0)
                .to_numpy()
            )
        status, condition = network.optimize(
            snapshots=window, **optimize_options
        )
        statuses.append(str(status).lower())
        conditions.append(str(condition).lower())
        objective = network.objective
        if (
            str(status).lower() != "ok"
            or objective is None
            or not np.isfinite(float(objective))
        ):
            return str(status), str(condition), objective
        objectives.append(float(objective))
    condition = "optimal" if all(
        item == "optimal" for item in conditions
    ) else conditions[-1]
    status = "ok" if all(item == "ok" for item in statuses) else statuses[-1]
    return status, condition, float(sum(objectives))


def _add_load_shedding(network, snapshots: pd.DatetimeIndex, options: dict) -> None:
    if not bool(options["enabled"]):
        return
    if "load_shedding" not in network.carriers.index:
        network.add("Carrier", "load_shedding")
    profile = network.loads_t.p_set.reindex(snapshots)
    peak = profile.max().reindex(network.loads.index).fillna(0)
    buses = network.loads["bus"].astype(str)
    names = "load_shedding:" + network.loads.index.astype(str)
    network.add(
        "Generator",
        names,
        bus=buses.to_numpy(),
        carrier="load_shedding",
        p_nom=(peak.to_numpy() * float(options["capacity_multiplier"])),
        marginal_cost=float(options["cost_eur_per_mwh"]),
    )


def _generator_output(network, snapshots, *, carrier: str) -> pd.DataFrame:
    selected = network.generators.index[
        network.generators["carrier"].eq(carrier)
    ]
    return network.generators_t.p.reindex(index=snapshots, columns=selected).fillna(0)


def _validate_dispatch(
    network,
    snapshots: pd.DatetimeIndex,
    *,
    tolerance: float,
) -> dict[str, object]:
    """Reject incomplete solver iterates before they become reusable outputs."""

    generator = network.generators_t.p.reindex(
        index=snapshots, columns=network.generators.index
    )
    storage = network.storage_units_t.p.reindex(
        index=snapshots, columns=network.storage_units.index
    )
    load = network.loads_t.p_set.reindex(index=snapshots)
    arrays = [frame.to_numpy(dtype=float) for frame in (generator, storage, load)]
    finite = all(np.isfinite(values).all() for values in arrays)
    generator_min = float(np.nanmin(arrays[0])) if arrays[0].size else 0.0
    storage_limit = network.storage_units["p_nom"].to_numpy(dtype=float)
    storage_excess = (
        float(np.maximum(np.abs(arrays[1]) - storage_limit, 0).max())
        if arrays[1].size else 0.0
    )
    valid = finite and generator_min >= -tolerance and storage_excess <= tolerance
    return {
        "dispatch_valid": bool(valid),
        "generator_min_mw": generator_min,
        "storage_limit_excess_mw": storage_excess,
    }
