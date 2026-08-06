"""PyPSA production simulation built only from PowerSystemCase."""

from __future__ import annotations

from pathlib import Path
import tomllib
import warnings

import numpy as np
import pandas as pd

from ...case import PowerSystemCase
from .formulation import formulation_table
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
        snapshots = _snapshots(network.snapshots, start, end)
        if snapshots.empty:
            raise ValueError("The selected UC time range contains no snapshots.")
        if str(options["commitment_mode"]) != "continuous":
            raise NotImplementedError(
                "Only continuous clustered dispatch is currently validated."
            )
        _add_load_shedding(network, snapshots, self.config["reliability"])
        solver = self.config["solver"]
        status, condition = network.optimize(
            snapshots=snapshots,
            solver_name=str(solver["name"]),
            solver_options={
                "time_limit": float(solver["time_limit_s"]),
                "mip_rel_gap": float(solver["mip_gap"]),
            },
            log_to_console=bool(solver["log_to_console"]),
            include_objective_constant=False,
        )
        condition_name = str(condition).lower()
        objective = network.objective
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
        }, name="value")
        return UnitCommitmentResult(
            self.case, network, str(status), str(condition), snapshots, summary
        )


def _snapshots(
    snapshots: pd.Index,
    start: object,
    end: object,
) -> pd.DatetimeIndex:
    index = pd.DatetimeIndex(snapshots)
    start = index.min() if start is None else pd.Timestamp(start)
    end = index.max() if end is None else pd.Timestamp(end)
    return index[(index >= start) & (index <= end)]


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
