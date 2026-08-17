"""Public manager for checking, building, and loading standard datasets."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import tomllib

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

from ..raw import RawDataManager
from .generator import _GeneratorStandardizer
from .load import _LoadStandardizer
from .model import StandardData, StandardNetwork
from .network import _NetworkStandardizer
from .parameter import (
    DEFAULT_QUALITY_ORDER,
    _ParameterStandardizer,
    as_parameter_data,
)
from .population import _PopulationStandardizer
from .plot import PlotResult
from .resource import _ResourceStandardizer
from .schema import (
    DATASET_IDS,
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
        "generator": _GeneratorStandardizer,
        "storage": _StorageStandardizer,
        "parameter": _ParameterStandardizer,
        "load": _LoadStandardizer,
        "population": _PopulationStandardizer,
        "resource": _ResourceStandardizer,
    }
    _DEPENDENCIES = {"load": ("spatial",)}
    _DEPENDENTS = {"spatial": {"load"}}

    def __init__(
        self,
        config_path: str | Path = "config/standard_data.toml",
        *,
        raw_data: RawDataManager | None = None,
    ) -> None:
        config_path = Path(config_path).expanduser().resolve()
        self.project_root = config_path.parent.parent
        with config_path.open("rb") as file:
            self.config = tomllib.load(file)
        self.datasets = self.config["datasets"]
        missing = set(DATASET_IDS).difference(self.datasets)
        if missing:
            raise ValueError(f"Standard data config is missing: {sorted(missing)}")
        self.output_root = (
            self.project_root / self.config["general"]["output_root"]
        )
        self.raw_data = raw_data or RawDataManager(
            self.project_root / "config/raw_data_sources.csv",
            self.project_root / "data",
        )

    def check(
        self,
        dataset_ids: str | Iterable[str] | None = None,
    ) -> pd.DataFrame:
        """Report raw-source and materialized-output availability."""

        selected = self._select(dataset_ids)
        dependency_ids = list(dict.fromkeys(
            dependency
            for dataset_id in selected
            for dependency in self._dependency_closure(dataset_id)
        ))
        checked_ids = list(dict.fromkeys([*selected, *dependency_ids]))
        source_ids_by_dataset = {
            dataset_id: self._source_ids(dataset_id)
            for dataset_id in checked_ids
        }
        source_ids = list(dict.fromkeys(
            source_id
            for values in source_ids_by_dataset.values()
            for source_id in values
        ))
        source_report = self.raw_data.check(source_ids)
        directly_ready = {
            dataset_id: bool(
                source_report.loc[source_ids_by_dataset[dataset_id], "available"].all()
            )
            for dataset_id in checked_ids
        }
        outputs_available = {
            dataset_id: all(
                path.exists() for path in self._output_paths(dataset_id)
            )
            for dataset_id in checked_ids
        }
        rows = []
        for dataset_id in selected:
            source_ids = self._source_ids(dataset_id)
            inputs_available = (
                directly_ready[dataset_id]
                and all(
                    outputs_available[name] or directly_ready[name]
                    for name in self._dependency_closure(dataset_id)
                )
            )
            available = outputs_available[dataset_id]
            rows.append({
                "dataset_id": dataset_id,
                "source_ids": tuple(source_ids),
                "inputs_available": inputs_available,
                "available": available,
                "status": (
                    "available"
                    if available
                    else "ready_to_build"
                    if inputs_available
                    else "input_unavailable"
                ),
            })
        return pd.DataFrame(rows).set_index("dataset_id")

    def build(
        self,
        dataset_ids: str | Iterable[str] | None = None,
        *,
        overwrite: bool = False,
    ) -> pd.DataFrame:
        """Build missing datasets and return their final check report."""

        selected = self._select(dataset_ids)
        initial = self.check(selected)
        pending = {
            dataset_id
            for dataset_id in selected
            if overwrite or not bool(initial.loc[dataset_id, "available"])
        }
        for dataset_id in tuple(pending):
            pending.update(
                dependency
                for dependency in self._dependency_closure(dataset_id)
                if not self.check(dependency).at[dependency, "available"]
            )
        for dataset_id in tuple(pending):
            pending.update(
                dependent
                for dependent in self._DEPENDENTS.get(dataset_id, ())
                if dependent in selected
                or self.check(dependent).at[dependent, "available"]
            )
        for dataset_id in DATASET_IDS:
            if dataset_id not in pending:
                continue
            status = self.check(dataset_id).loc[dataset_id]
            if not bool(status["inputs_available"]):
                continue
            processor = self._PROCESSORS[self.datasets[dataset_id]["processor"]]
            data = processor(self, dataset_id).build()
            _validate_dataset(data, dataset_id)
        return self.check(selected)

    def load(self, dataset_id: str | None = None) -> object:
        """Load one dataset, or all datasets as a self-contained snapshot."""

        if dataset_id is None:
            return StandardData(
                **{name: self.load(name) for name in DATASET_IDS},
                config=self.config,
            )
        self._select(dataset_id)
        paths = self._output_paths(dataset_id)
        if not all(path.exists() for path in paths):
            raise FileNotFoundError(
                f"{dataset_id} has not been built. Run build({dataset_id!r})."
            )
        if dataset_id == "network":
            data = StandardNetwork(
                _read_geodataframe(paths[0]),
                _read_geodataframe(paths[1]),
                _read_geodataframe(paths[2]),
                _read_geodataframe(paths[3]),
            )
        elif dataset_id in {"spatial", "generator", "storage", "population"}:
            data = _read_geodataframe(paths[0])
        elif dataset_id == "parameter":
            data = as_parameter_data(_read_dataframe(paths[0]))
            options = self.datasets["parameter"].get("options", {})
            data.attrs["quality_order"] = tuple(
                options.get("quality_order", DEFAULT_QUALITY_ORDER)
            )
            data.attrs["name_aliases"] = dict(
                options.get("name_aliases", {})
            )
            data.attrs["conflict_tolerance"] = float(
                options.get("conflict_tolerance", 1e-6)
            )
        else:
            data = xr.open_dataset(paths[0], chunks="auto")
        if dataset_id == "population":
            data["class"] = "cell_population"
            data["geometry_method"] = "aggregated_source_cell"
        elif dataset_id == "resource":
            data = data.assign_coords(
                geometry_method=(
                    "uid", ["source_cell_centroid"] * data.sizes["uid"]
                )
            )
        _validate_dataset(data, dataset_id)
        return data

    def plot(self, dataset_id: str, **kwargs: object) -> PlotResult:
        """Return one representative figure without writing an output file."""

        from ..plot import plot_standard

        data = self.load(dataset_id)
        spatial = kwargs.pop("spatial", None)
        if spatial is None and dataset_id in {
            "network", "generator", "storage", "load", "population", "resource",
        }:
            spatial = self.load("spatial")
        figure = plot_standard(
            data, dataset_id, spatial=spatial, **kwargs
        )
        if isinstance(data, xr.Dataset):
            data.close()
        return figure

    def schema(
        self,
        dataset_ids: str | Iterable[str] | None = None,
    ) -> pd.DataFrame:
        """Describe actual columns or arrays in materialized standard data."""

        selected = self._select(dataset_ids)
        available = self.check(selected)["available"]
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

    def _source_ids(self, dataset_id: str) -> list[str]:
        """Return raw dependencies declared directly or by source adapters."""

        config = self.datasets[dataset_id]
        if "source_ids" in config:
            return list(config["source_ids"])
        specs = config.get("options", {}).get("sources", ())
        return list(dict.fromkeys(str(spec["source_id"]) for spec in specs))

    def _dependency_closure(self, dataset_id: str) -> tuple[str, ...]:
        dependencies: list[str] = []
        for dependency in self._DEPENDENCIES.get(dataset_id, ()):
            dependencies.extend(self._dependency_closure(dependency))
            dependencies.append(dependency)
        return tuple(dict.fromkeys(dependencies))

    def _output_paths(self, dataset_id: str) -> list[Path]:
        config = self.datasets[dataset_id]
        if dataset_id == "network":
            directory = self.output_root / config["output"]
            return [
                directory / f"{component}.parquet"
                for component in ("bus", "branch", "transformer", "converter")
            ]
        filenames = (
            list(config["outputs"].values())
            if "outputs" in config
            else [config["output"]]
        )
        return [self.output_root / filename for filename in filenames]
