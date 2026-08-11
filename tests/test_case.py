"""Focused regression tests for backend-neutral case helpers."""

import numpy as np
import pandas as pd
from types import SimpleNamespace
import xarray as xr

from src.app import UnitCommitmentApplication
from src.app.uc.manager import _optimize, _validate_dispatch
from src.case.backends.manifest import load_pypsa_manifest
from src.case.aggregate import aggregate_load
from src.case.manager import _validate_load_profiles
from src.case.time import select_time


def _load() -> xr.Dataset:
    return xr.Dataset(
        {
            "demand_mw": (
                ("time", "uid", "class"),
                np.arange(12, dtype=float).reshape(3, 2, 2),
            )
        },
        coords={
            "time": pd.date_range("2024-01-01", periods=3, freq="1h"),
            "uid": ["cell:a", "cell:b"],
            "class": ["industry", "residential"],
            "spatial_uid": ("uid", ["cell:a", "cell:b"]),
            "bus_uid": ("uid", ["bus:1", "bus:1"]),
        },
        attrs={"timezone": "Asia/Shanghai", "time_step": "1h"},
    )


def test_bus_load_aggregation_conserves_power() -> None:
    source = _load()
    result = aggregate_load(source, "bus")
    np.testing.assert_allclose(
        result.demand_mw.sum("uid"), source.demand_mw.sum("uid")
    )
    assert result.uid.values.tolist() == ["bus:1"]


def test_load_profile_validation_checks_bus_values_and_conservation() -> None:
    cells = _load().assign_coords(
        spatial_level=("uid", ["province", "marine_zone"])
    )
    cells["demand_mw"].loc[{"uid": "cell:b"}] = 0.0
    buses = pd.DataFrame({"uid": ["bus:1"]})
    population = pd.DataFrame({
        "spatial_uid": ["cell:a", "cell:b"], "population": [100.0, 0.0]
    })
    result = aggregate_load(cells, "bus")
    report = _validate_load_profiles(
        result, cells, buses, population
    ).set_index("name")

    assert report.at["electrical_bus_mapping", "status"] == "pass"
    assert report.at["finite_values", "status"] == "pass"
    assert report.at["aggregate_conservation", "status"] == "pass"
    assert report.at["zero_auxiliary_load", "status"] == "pass"


def test_bus_load_aggregation_preserves_missing_input_signal() -> None:
    source = _load()
    source["demand_mw"].loc[{"uid": "cell:b"}] = np.nan
    result = aggregate_load(source, "bus")

    assert np.isnan(result["demand_mw"].sel(uid="bus:1")).all()


def test_time_alignment_supports_coarse_and_fine_steps() -> None:
    source = _load()
    common = {
        "start": "2024-01-01 00:00",
        "end": "2024-01-01 02:00",
        "timezone": "Asia/Shanghai",
    }
    assert select_time(source, {**common, "time_step": "2h"}).sizes["time"] == 2
    assert select_time(source, {**common, "time_step": "30min"}).sizes["time"] == 5


def test_pypsa_manifest_controls_units_and_fallbacks() -> None:
    manifest = load_pypsa_manifest("config/pypsa_parameter_manifest.toml")
    assets = pd.DataFrame({"uid": ["branch:1", "branch:2"]})
    parameters = pd.DataFrame({
        "uid": ["branch:1", "branch:2"],
        "name": ["r_ohm_per_km", "r_ohm_per_km"],
        "value": [0.04, 0.05],
        "unit": ["ohm/km", "invalid"],
    })
    values = manifest.values("branch_ac_r", assets, parameters)
    np.testing.assert_allclose(values, [0.04, 0.03])
    assert manifest.table().set_index("key").at[
        "branch_ac_r", "target"
    ] == "Line.r"
    empty = pd.DataFrame(columns=("uid", "name", "value", "unit"))
    component_assets = {
        name: pd.DataFrame({"uid": []})
        for name in ("transformer", "converter", "generator", "storage")
    }
    component_assets["branch"] = assets.assign(current_type="AC")
    report = manifest.validate(
        {"branch": parameters, **{name: empty for name in component_assets if name != "branch"}},
        component_assets,
    ).set_index("name")
    assert report.at["branch_ac_r", "status"] == "fallback"
    assert report.at["branch_ac_r", "value"] == 0.5

    storage = pd.DataFrame({
        "uid": ["storage:1", "storage:2", "storage:3"],
        "duration_h": [8.0, np.nan, np.nan],
    })
    storage_parameters = pd.DataFrame({
        "uid": ["storage:2"],
        "name": ["energy_to_power_ratio"],
        "value": [4.0],
        "unit": ["h"],
    })
    np.testing.assert_allclose(
        manifest.values("storage_max_hours", storage, storage_parameters),
        [8.0, 4.0, 6.0],
    )


def test_uc_list_marks_binary_commitment_inactive_in_continuous_mode() -> None:
    empty = SimpleNamespace(data=pd.DataFrame())
    case = SimpleNamespace(
        network=SimpleNamespace(
            branch=empty, transformer=empty, converter=empty
        ),
        storage=empty,
        config={"backend": {"pypsa": {
            "branch_extendable": False,
            "transformer_extendable": False,
            "converter_extendable": False,
            "generator_extendable": False,
            "storage_extendable": False,
        }}},
    )
    table = UnitCommitmentApplication(case, "config/uc.toml").list()
    status = table.loc[table["item"].eq("commitment status")].iloc[0]
    assert not status["active"]
    assert status["latex"] == r"u_{g,t}\in\{0,1\}"


def test_dispatch_validation_rejects_negative_generator_output() -> None:
    snapshots = pd.date_range("2024-01-01", periods=2, freq="1h")
    network = SimpleNamespace(
        generators=pd.DataFrame(index=["generator:1"]),
        generators_t=SimpleNamespace(
            p=pd.DataFrame([[1.0], [-2.0]], index=snapshots, columns=["generator:1"])
        ),
        storage_units=pd.DataFrame({"p_nom": [5.0]}, index=["storage:1"]),
        storage_units_t=SimpleNamespace(
            p=pd.DataFrame([[0.0], [0.0]], index=snapshots, columns=["storage:1"])
        ),
        loads_t=SimpleNamespace(
            p_set=pd.DataFrame([[1.0], [1.0]], index=snapshots, columns=["load:1"])
        ),
    )

    result = _validate_dispatch(network, snapshots, tolerance=1e-3)

    assert not result["dispatch_valid"]
    assert result["generator_min_mw"] == -2.0


def test_rolling_horizon_solves_each_window_and_carries_storage_state() -> None:
    snapshots = pd.date_range("2024-01-01", periods=4, freq="1h")

    class Network:
        def __init__(self):
            self.storage_units = pd.DataFrame(index=["storage:1"])
            self.storage_units_t = SimpleNamespace(
                state_of_charge=pd.DataFrame(
                    np.nan, index=snapshots, columns=self.storage_units.index
                )
            )
            self.objective = None
            self.windows = []

        def optimize(self, *, snapshots, **_):
            self.windows.append(tuple(snapshots))
            self.storage_units_t.state_of_charge.loc[snapshots] = (
                np.arange(1, len(snapshots) + 1)[:, None]
            )
            self.objective = 10.0
            return "ok", "optimal"

    network = Network()
    status, condition, objective = _optimize(
        network,
        snapshots,
        solve_mode="rolling_horizon",
        horizon=2,
        overlap=0,
    )

    assert status == "ok"
    assert condition == "optimal"
    assert objective == 20.0
    assert network.windows == [tuple(snapshots[:2]), tuple(snapshots[2:])]
    assert network.storage_units.at[
        "storage:1", "state_of_charge_initial"
    ] == 2.0
