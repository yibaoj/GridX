"""Public manager for checking, building, and loading standard datasets."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import tomllib

import pandas as pd
import xarray as xr
from matplotlib.figure import Figure

from ..raw import RawDataManager
from .generation import _GenerationStandardizer
from .load import _LoadStandardizer
from .network import _NetworkStandardizer
from .parameter import _ParameterStandardizer
from .population import _PopulationStandardizer
from .plot import PLOTTERS
from .resource import _ResourceStandardizer
from .schema import (
    DATASET_IDS,
    NetworkData,
    _SCHEMA_COLUMNS,
    _dataset_schema,
    _read_dataframe,
    _read_geodataframe,
    _validate_dataset,
)
from .spatial import _SpatialStandardizer
from .storage import _StorageStandardizer


class StandardDataManager:
    """Build and load stable datasets through source-specific standardizers."""

    _PROCESSORS = {
        "spatial": _SpatialStandardizer,
        "network": _NetworkStandardizer,
        "generation": _GenerationStandardizer,
        "storage": _StorageStandardizer,
        "parameter": _ParameterStandardizer,
        "load": _LoadStandardizer,
        "population": _PopulationStandardizer,
        "resource": _ResourceStandardizer,
    }

    def __init__(
        self,
        config_path: str | Path = "config/standard_data.toml",
        *,
        raw_data: RawDataManager | None = None,
    ) -> None:
        config_path = Path(config_path).expanduser().resolve()
        self.project_root = config_path.parent.parent
        with config_path.open("rb") as file:
            config = tomllib.load(file)
        self.datasets = config["datasets"]
        missing = set(DATASET_IDS).difference(self.datasets)
        if missing:
            raise ValueError(f"Standard data config is missing: {sorted(missing)}")
        self.output_root = self.project_root / config["general"]["output_root"]
        self.raw_data = raw_data or RawDataManager(
            self.project_root / "config/raw_data_sources.csv",
            self.project_root / "data",
        )

    def check(
        self,
        dataset_ids: str | Iterable[str] | None = None,
    ) -> pd.DataFrame:
        rows = []
        for dataset_id in self._select(dataset_ids):
            config = self.datasets[dataset_id]
            source_report = self.raw_data.check(config["source_ids"])
            outputs = self._output_paths(dataset_id)
            rows.append({
                "dataset_id": dataset_id,
                "processor": config["processor"],
                "source_ids": tuple(config["source_ids"]),
                "sources_available": bool(source_report["exists"].all()),
                "source_status": "; ".join(
                    f"{source}:{status}"
                    for source, status in source_report["status"].items()
                ),
                "outputs": tuple(str(path) for path in outputs),
                "output_available": all(path.exists() for path in outputs),
            })
        return pd.DataFrame(rows).set_index("dataset_id")

    def build(
        self,
        dataset_ids: str | Iterable[str],
    ) -> object:
        selected = self._select(dataset_ids)
        results = {}
        for dataset_id in selected:
            missing_sources = self.raw_data.check(
                self.datasets[dataset_id]["source_ids"]
            ).query("not exists")
            if not missing_sources.empty:
                raise FileNotFoundError(
                    f"{dataset_id} is missing raw sources: "
                    f"{list(missing_sources.index)}"
                )
            processor = self._PROCESSORS[self.datasets[dataset_id]["processor"]]
            data = processor(self, dataset_id).build()
            _validate_dataset(data, dataset_id)
            results[dataset_id] = data
        return results[selected[0]] if isinstance(dataset_ids, str) else results

    def load(self, dataset_id: str) -> object:
        self._select(dataset_id)
        paths = self._output_paths(dataset_id)
        if not all(path.exists() for path in paths):
            raise FileNotFoundError(
                f"{dataset_id} has not been built. Run build({dataset_id!r})."
            )
        if dataset_id == "network":
            data = NetworkData(
                _read_geodataframe(paths[0]),
                _read_geodataframe(paths[1]),
            )
        elif dataset_id in {"spatial", "generation", "storage", "population"}:
            data = _read_geodataframe(paths[0])
        elif dataset_id == "parameter":
            data = _read_dataframe(paths[0])
        else:
            data = xr.open_dataset(paths[0])
        _validate_dataset(data, dataset_id)
        return data

    def plot(self, dataset_id: str, **kwargs: object) -> Figure:
        """Return one representative figure without writing an output file."""

        data = self.load(dataset_id)
        if dataset_id in {"network", "population", "resource"}:
            kwargs.setdefault("spatial", self.load("spatial"))
        return PLOTTERS[dataset_id](data, **kwargs)

    def schema(
        self,
        dataset_ids: str | Iterable[str] | None = None,
    ) -> pd.DataFrame:
        """Describe actual columns or arrays in materialized standard data."""

        selected = self._select(dataset_ids)
        available = self.check(selected)["output_available"]
        if dataset_ids is not None and not available.all():
            raise FileNotFoundError(
                "Standard outputs are unavailable for: "
                f"{available.index[~available].tolist()}"
            )
        tables = []
        for dataset_id in available.index[available]:
            data = self.load(dataset_id)
            table = _dataset_schema(data)
            table.insert(0, "dataset_id", dataset_id)
            tables.append(table)
            if isinstance(data, xr.Dataset):
                data.close()
        result = (
            pd.concat(tables, ignore_index=True)
            if tables
            else pd.DataFrame(columns=("dataset_id", *_SCHEMA_COLUMNS))
        )
        result.attrs["unavailable_dataset_ids"] = tuple(
            available.index[~available]
        )
        return result

    def _select(
        self,
        dataset_ids: str | Iterable[str] | None,
    ) -> list[str]:
        selected = (
            list(DATASET_IDS)
            if dataset_ids is None
            else [dataset_ids]
            if isinstance(dataset_ids, str)
            else list(dataset_ids)
        )
        unknown = set(selected).difference(DATASET_IDS)
        if unknown:
            raise KeyError(f"Unknown dataset_id values: {sorted(unknown)}")
        return selected

    def _output_paths(self, dataset_id: str) -> list[Path]:
        config = self.datasets[dataset_id]
        filenames = (
            list(config["outputs"].values())
            if "outputs" in config
            else [config["output"]]
        )
        return [self.output_root / filename for filename in filenames]
