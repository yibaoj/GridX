"""Regression tests for the shared check/build/load manager contract."""

from pathlib import Path
from types import SimpleNamespace

import pandas as pd

import src.case.manager as case_manager_module
import src.plot as plotting
from src.case.manager import PowerSystemCaseManager
from src.case.model import PowerSystemCase
from src.mapping.model import MappedData
from src.mapping.manager import SpatiotemporalMappingManager
from src.standard.model import StandardData


class _AvailableStandardData:
    def check(self, dataset_ids=None) -> pd.DataFrame:
        selected = (
            [dataset_ids]
            if isinstance(dataset_ids, str)
            else list(dataset_ids or (
                "spatial", "network", "generator", "storage", "parameter",
                "load", "population", "resource",
            ))
        )
        return pd.DataFrame(
            {"dataset_id": selected, "available": True}
        ).set_index("dataset_id")


def _mapping_manager(tmp_path: Path) -> SpatiotemporalMappingManager:
    manager = object.__new__(SpatiotemporalMappingManager)
    manager.outputs = {
        name: tmp_path / ("network" if name == "network" else f"{name}.data")
        for name in SpatiotemporalMappingManager._OUTPUTS_BY_DATASET
    }
    manager.standard_data = _AvailableStandardData()
    manager.config = {
        "spatial_mapping": {
            "load": {"method": "auxiliary", "auxiliary_dataset": "population"}
        }
    }
    manager.outputs["spatial"].touch()
    manager.outputs["network"].mkdir()
    for path in manager._output_paths("network"):
        path.touch()
    return manager


def test_mapping_build_skips_available_output_and_honors_overwrite(
    tmp_path: Path,
) -> None:
    manager = _mapping_manager(tmp_path)
    manager.outputs["generator"].touch()
    calls = []
    manager._build_products = lambda pending: calls.append(pending)

    report = manager.build("generator")
    assert report.at["generator", "status"] == "available"
    assert calls == []

    manager.build("generator", overwrite=True)
    assert calls == [{"generator"}]


def test_mapping_rebuilds_existing_dependents_of_missing_spatial(
    tmp_path: Path,
) -> None:
    manager = _mapping_manager(tmp_path)
    manager.outputs["spatial"].unlink()
    manager.outputs["generator"].touch()
    calls = []
    manager._build_products = lambda pending: calls.append(pending)

    manager.build(["spatial", "generator"])

    assert calls == [{"spatial", "network", "generator"}]


def test_mapping_parameter_does_not_require_spatial_outputs(
    tmp_path: Path,
) -> None:
    manager = _mapping_manager(tmp_path)
    manager.outputs["spatial"].unlink()
    calls = []
    manager._build_products = lambda pending: calls.append(pending)

    manager.build("parameter")

    assert calls == [{"parameter"}]


def test_data_models_declare_parameter_data() -> None:
    assert StandardData.__annotations__["parameter"] == "ParameterData"
    assert MappedData.__annotations__["parameter"] == "ParameterData"


def test_case_build_returns_report_and_rebuilds_only_when_needed() -> None:
    manager = object.__new__(PowerSystemCaseManager)
    calls = []
    report = pd.DataFrame({
        "inputs_available": [True],
        "available": [True],
        "case_complete": [True],
        "status": ["available"],
    }, index=pd.Index(["network"], name="dataset_id"))
    manager.check = lambda dataset_ids=None: report
    manager._build_case = lambda: calls.append("build")

    assert manager.build().equals(report)
    assert calls == []
    assert manager.build(overwrite=True).equals(report)
    assert calls == ["build"]


def test_case_manager_reuses_and_closes_complete_case(monkeypatch) -> None:
    manager = object.__new__(PowerSystemCaseManager)
    manager.output_root = Path("unused")
    manager.config = {}
    manager._case_cache = None
    loaded = []

    class _Case:
        closed = False

        def close(self) -> None:
            self.closed = True

    case = _Case()
    monkeypatch.setattr(case_manager_module, "case_outputs", lambda _: ())
    monkeypatch.setattr(
        case_manager_module, "load_case",
        lambda *_: loaded.append(case) or case,
    )

    assert manager.load() is case
    assert manager.load() is case
    assert loaded == [case]
    manager.close()
    assert case.closed
    assert manager._case_cache is None


def test_data_objects_share_plot_api(monkeypatch) -> None:
    calls = []
    standard = StandardData(*([None] * 8), config={})
    mapped = MappedData(
        *([None] * 8), config={"general": {"metric_crs": "EPSG:4326"}}
    )
    case = PowerSystemCase(*([None] * 8), config={})
    monkeypatch.setattr(
        plotting, "plot_standard",
        lambda data, dataset_id, **kwargs: calls.append(("standard", dataset_id)),
    )
    monkeypatch.setattr(
        plotting, "plot_mapped",
        lambda data, dataset_id, **kwargs: calls.append(("mapping", dataset_id)),
    )
    monkeypatch.setattr(
        "src.case.plot.plot_case",
        lambda data, dataset_id, **kwargs: calls.append(("case", dataset_id)),
    )

    standard.plot("network")
    mapped.plot("network")
    case.plot("network")
    assert calls == [
        ("standard", "network"),
        ("mapping", "network"),
        ("case", "network"),
    ]
