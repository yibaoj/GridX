"""Public manager for the spatiotemporal mapping layer."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import tomllib

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

from ..standard import DATASET_IDS, StandardNetwork, StandardDataManager
from ..standard.parameter import (
    DEFAULT_QUALITY_ORDER,
    as_parameter_data,
)
from ..standard.plot import PlotResult
from .cell import build_spatial_cells
from .model import MappedData, MappedNetwork
from .network import (
    attach_bus_coordinates,
    attach_bus_mapping,
    largest_connected_network,
    map_branches_to_cells,
    map_objects_to_cells,
    map_to_buses,
)
from .space import aggregate_extensive, map_timeseries_to_cells
from .time import align_time
from .schema import annotate_schema, mapping_schema


class SpatiotemporalMappingManager:
    """Build explicit spatial-cell and electrical-bus mappings."""

    _OUTPUTS_BY_DATASET = {
        "spatial": ("spatial",),
        "population": ("population",),
        "load": ("load",),
        "resource": ("resource",),
        "network": ("network",),
        "generator": ("generator",),
        "storage": ("storage",),
        "parameter": ("parameter",),
    }
    _DEPENDENTS = {
        "spatial": {
            "population", "load", "resource", "network", "generator", "storage",
        },
        "population": {"load"},
        "network": {"load", "generator", "storage"},
    }
    _STANDARD_DEPENDENCIES = {
        "spatial": ("spatial",),
        "population": ("spatial", "population"),
        "load": ("spatial", "load", "network"),
        "resource": ("spatial", "resource"),
        "network": ("spatial", "network"),
        "generator": ("spatial", "network", "generator"),
        "storage": ("spatial", "network", "storage"),
        "parameter": ("parameter",),
    }

    def __init__(
        self,
        config_path: str | Path = "config/mapping.toml",
        standard_data: StandardDataManager | None = None,
    ) -> None:
        path = Path(config_path).expanduser()
        if not path.is_absolute() and not path.exists():
            path = Path(__file__).resolve().parents[2] / path
        path = path.resolve()
        self.project_root = path.parent.parent
        with path.open("rb") as file:
            self.config = tomllib.load(file)
        self.options = self.config["general"]
        self.outputs = {
            name: self.project_root / self.options["output_root"] / filename
            for name, filename in self.config["outputs"].items()
        }
        self.standard_data = standard_data or StandardDataManager(
            self.project_root / "config/standard_data.toml"
        )

    def build_cells(self) -> gpd.GeoDataFrame:
        """Build only the clipped standard spatial cells."""

        cells = build_spatial_cells(
            self.standard_data.load("spatial"),
            self.config["cell"],
            metric_crs=self.options["metric_crs"],
            project_root=self.project_root,
        )
        annotate_schema(cells, "spatial")
        self._write_geodataframe("spatial", cells)
        return cells

    def build_network(self) -> StandardNetwork:
        """Return the largest connected subgraph of the standard network."""

        return largest_connected_network(self.standard_data.load("network"))

    def build(
        self,
        dataset_ids: str | Iterable[str] | None = None,
        *,
        overwrite: bool = False,
    ) -> pd.DataFrame:
        """Build missing mapped products and return their final check report."""

        selected = self._select(dataset_ids)
        initial = self.check()
        pending = set(
            name for name in selected
            if (
                (overwrite or not bool(initial.at[name, "available"]))
                and bool(initial.at[name, "inputs_available"])
            )
        )
        if pending:
            pending = self._expand_pending(pending, selected, initial)
            self._build_products(pending)
        return self.check(selected)

    def load(self, dataset_id: str | None = None) -> object:
        """Load one mapped dataset, or all datasets as :class:`MappedData`."""

        if dataset_id is None:
            return MappedData(
                **{name: self.load(name) for name in DATASET_IDS},
                config=self.config,
            )
        self._select(dataset_id)
        missing = [
            name for name in self._OUTPUTS_BY_DATASET[dataset_id]
            if not all(path.exists() for path in self._output_paths(name))
        ]
        if missing:
            raise FileNotFoundError(f"Mapping outputs are unavailable: {missing}")
        if dataset_id == "network":
            return self._read_network()
        if dataset_id in {"spatial", "population"}:
            return self._read_geodataframe(dataset_id, dataset_id)
        if dataset_id in {"resource", "load"}:
            return self._read_xarray(dataset_id, dataset_id)
        if dataset_id == "parameter":
            return self._read_parameter()
        return self._read_geodataframe(dataset_id, dataset_id)

    def check(
        self,
        dataset_ids: str | Iterable[str] | None = None,
    ) -> pd.DataFrame:
        """Report whether selected mapped datasets are available."""

        rows = []
        standard_status = self.standard_data.check()
        for dataset_id in self._select(dataset_ids):
            dependencies = list(self._STANDARD_DEPENDENCIES[dataset_id])
            load_options = self.config["spatial_mapping"]["load"]
            if dataset_id == "load" and load_options["method"] == "auxiliary":
                dependencies.append(str(load_options["auxiliary_dataset"]))
            inputs_available = bool(
                standard_status.loc[dependencies, "available"].all()
            )
            available = all(
                path.exists()
                for name in self._OUTPUTS_BY_DATASET[dataset_id]
                for path in self._output_paths(name)
            )
            rows.append({
                "dataset_id": dataset_id,
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

    def plot(self, dataset_id: str, **kwargs: object) -> PlotResult:
        """Return one mapped-data figure without writing an output file."""

        from ..visualization import plot_mapped

        data = self.load(dataset_id)
        spatial = kwargs.pop("spatial", None)
        cells = kwargs.pop("cells", None)
        if dataset_id != "parameter":
            spatial = (
                self.standard_data.load("spatial")
                if spatial is None else spatial
            )
            cells = self.load("spatial") if cells is None else cells
        figure = plot_mapped(
            data, dataset_id, cells=cells, spatial=spatial,
            **kwargs,
        )
        if isinstance(data, xr.Dataset):
            data.close()
        return figure

    def schema(
        self,
        dataset_ids: str | Iterable[str] | None = None,
    ) -> pd.DataFrame:
        """Describe actual structures in selected mapped datasets."""

        selected = self._select(dataset_ids)
        available = self.check(selected)["available"]
        if dataset_ids is not None and not available.all():
            raise FileNotFoundError(
                "Mapping outputs are unavailable for: "
                f"{available.index[~available].tolist()}"
            )
        tables = []
        for name in available.index[available]:
            data = self.load(name)
            tables.append(mapping_schema(data, name))
            if isinstance(data, xr.Dataset):
                data.close()
        result = (
            pd.concat(tables, ignore_index=True)
            if tables
            else pd.DataFrame(columns=("dataset_id",))
        )
        result.attrs["unavailable_dataset_ids"] = tuple(
            available.index[~available]
        )
        return result

    def _expand_pending(
        self,
        pending: set[str],
        selected: list[str],
        status: pd.DataFrame,
    ) -> set[str]:
        """Add unavailable prerequisites and existing affected dependents."""

        pending = set(pending)
        while True:
            before = set(pending)
            if pending.difference({"spatial", "parameter"}) and not status.at[
                "spatial", "available"
            ]:
                pending.add("spatial")
            uses_population = (
                self.config["spatial_mapping"]["load"]["method"] == "auxiliary"
                and self.config["spatial_mapping"]["load"].get(
                    "auxiliary_dataset"
                ) == "population"
            )
            if (
                "load" in pending
                and uses_population
                and not status.at["population", "available"]
            ):
                pending.add("population")
            if (
                pending.intersection({"load", "generator", "storage"})
                and not status.at["network", "available"]
            ):
                pending.add("network")
            for name in tuple(pending):
                for dependent in self._DEPENDENTS.get(name, ()):
                    if dependent in selected or status.at[dependent, "available"]:
                        pending.add(dependent)
            if pending == before:
                return pending

    def _build_products(self, pending: set[str]) -> None:
        """Materialize requested products while reusing available prerequisites."""

        pending = set(pending)
        if "parameter" in pending:
            parameter = self.standard_data.load("parameter")
            self.outputs["parameter"].parent.mkdir(parents=True, exist_ok=True)
            parameter.to_parquet(self.outputs["parameter"], index=False)
            pending.remove("parameter")
        if not pending:
            return

        cells = (
            self.build_cells()
            if "spatial" in pending or not self.check("spatial").at[
                "spatial", "available"
            ]
            else self.load("spatial")
        )
        nominal_cell_area = (
            float(self.config["cell"]["size_km"]) ** 2
            if self.config["cell"]["kind"] == "square"
            else float(cells["area_km2"].median())
        )
        spatial_options = self.config["spatial_mapping"]
        load_options = spatial_options["load"]
        resource_options = spatial_options["resource"]
        load_method = str(load_options["method"])
        auxiliary_dataset = str(load_options.get("auxiliary_dataset", ""))
        population = None
        if "population" in pending:
            population_source = self.standard_data.load("population")
            population = aggregate_extensive(
                population_source,
                cells,
                value_column="population",
                metric_crs=self.options["metric_crs"],
                nominal_cell_area_km2=nominal_cell_area,
                require_finer=(
                    bool(load_options["auxiliary_must_be_finer"])
                    and load_method == "auxiliary"
                    and auxiliary_dataset == "population"
                ),
            )
            annotate_schema(population, "population")
            self._write_geodataframe("population", population)
        elif "load" in pending and auxiliary_dataset == "population":
            population = self.load("population")

        auxiliary_cells = None
        auxiliary_value = None
        if "load" in pending and load_method == "auxiliary":
            if not auxiliary_dataset:
                raise ValueError(
                    "Auxiliary load downscaling requires auxiliary_dataset."
                )
            auxiliary_value = str(load_options["auxiliary_value"])
            if auxiliary_dataset == "population":
                auxiliary_cells = population
            else:
                auxiliary_cells = aggregate_extensive(
                    self.standard_data.load(auxiliary_dataset),
                    cells,
                    value_column=auxiliary_value,
                    metric_crs=self.options["metric_crs"],
                    nominal_cell_area_km2=nominal_cell_area,
                    require_finer=bool(
                        load_options["auxiliary_must_be_finer"]
                    ),
                )
        elif "load" in pending and load_method != "linear":
            raise ValueError(
                "Load downscaling method must be 'linear' or 'auxiliary'."
            )
        load = None
        if "load" in pending:
            load_source = self.standard_data.load("load")
            load = map_timeseries_to_cells(
                align_time(
                    load_source,
                    timezone=self.options["timezone"],
                    time_step=self.options["time_step"],
                ),
                cells,
                variable="demand_mw",
                quantity_kind="extensive",
                method=load_method,
                metric_crs=self.options["metric_crs"],
                auxiliary_cells=auxiliary_cells,
                auxiliary_value=auxiliary_value,
                uncovered_auxiliary_nearest_levels=list(
                    load_options.get("uncovered_auxiliary_nearest_levels", [])
                ),
                conservation_tolerance=float(
                    load_options["conservation_tolerance"]
                ),
            )
            load_source.close()

        if "resource" in pending:
            resource_source = self.standard_data.load("resource")
            resource = map_timeseries_to_cells(
                align_time(
                    resource_source,
                    timezone=self.options["timezone"],
                    time_step=self.options["time_step"],
                ),
                cells,
                variable="availability_pu",
                quantity_kind="intensive",
                method=str(resource_options["method"]),
                metric_crs=self.options["metric_crs"],
                source_cell_width_degrees=float(
                    resource_options["source_cell_width_degrees"]
                ),
                source_cell_height_degrees=float(
                    resource_options["source_cell_height_degrees"]
                ),
            )
            self._write_xarray("resource", resource, "availability_pu")
            resource_source.close()

        needs_network = bool(
            {"network", "generator", "storage", "load"}.intersection(pending)
        )
        if needs_network:
            if "network" in pending or not self.check("network").at[
                "network", "available"
            ]:
                connected_network = self.build_network()
                network = StandardNetwork(
                    map_objects_to_cells(
                        connected_network.bus,
                        cells,
                        metric_crs=self.options["metric_crs"],
                    ),
                    connected_network.branch,
                    connected_network.transformer,
                    connected_network.converter,
                )
                for component in ("branch", "transformer", "converter"):
                    getattr(network, component).attrs[
                        "mapping_dataset_id"
                    ] = "network"
                annotate_schema(network.bus, "network", "bus")
                annotate_schema(network.branch, "network", "branch")
                annotate_schema(network.transformer, "network", "transformer")
                annotate_schema(network.converter, "network", "converter")
                branch_cells = map_branches_to_cells(
                    network.branch,
                    cells,
                    metric_crs=self.options["metric_crs"],
                )
                branch_cells.attrs["mapping_dataset_id"] = "network"
            else:
                network = self.load("network")
                branch_cells = network.branch_mapping

        if needs_network:
            asset_options = self.config["asset_bus_mapping"]
            bus_subclasses = list(
                self.config["network"].get("bus_subclasses", [])
            )
            common = {
                "buses": network.bus,
                "cells": cells,
                "source_uid_column": "uid",
                "prefer_same_admin": bool(asset_options["prefer_same_admin"]),
                "metric_crs": self.options["metric_crs"],
                "random_seed": int(self.options["random_seed"]),
                "bus_subclasses": bus_subclasses,
                "voltage_preference": str(asset_options["voltage_preference"]),
            }
        if "generator" in pending:
            generator = map_objects_to_cells(
                self.standard_data.load("generator"),
                cells,
                metric_crs=self.options["metric_crs"],
            )
            generator_bus = map_to_buses(
                generator,
                output_uid_column="generator_uid",
                method=str(asset_options["generator_method"]),
                **common,
            )
            generator = annotate_schema(
                attach_bus_mapping(
                    generator,
                    generator_bus,
                    source_uid_column="generator_uid",
                ),
                "generator",
            )
            self._write_geodataframe("generator", generator)
        if "storage" in pending:
            storage = map_objects_to_cells(
                self.standard_data.load("storage"),
                cells,
                metric_crs=self.options["metric_crs"],
            )
            storage_bus = map_to_buses(
                storage,
                output_uid_column="storage_uid",
                method=str(asset_options["storage_method"]),
                **common,
            )
            storage = annotate_schema(
                attach_bus_mapping(
                    storage,
                    storage_bus,
                    source_uid_column="storage_uid",
                ),
                "storage",
            )
            self._write_geodataframe("storage", storage)
        if "load" in pending:
            load_cells = cells.copy()
            load_cells["geometry"] = load_cells["centre_geometry"]
            load_cells = load_cells.set_geometry("geometry")
            load_bus_options = self.config["load_bus_mapping"]
            load_bus = map_to_buses(
                load_cells,
                network.bus,
                cells,
                source_uid_column="spatial_uid",
                output_uid_column="load_spatial_uid",
                method=str(load_bus_options["method"]),
                prefer_same_admin=bool(load_bus_options["prefer_same_admin"]),
                metric_crs=self.options["metric_crs"],
                random_seed=int(self.options["random_seed"]),
                bus_subclasses=bus_subclasses,
                voltage_preference=str(load_bus_options["voltage_preference"]),
            )
            load = annotate_schema(
                attach_bus_coordinates(
                    load,
                    load_bus,
                    source_uid_column="load_spatial_uid",
                ),
                "load",
            )
            self._write_xarray("load", load, "demand_mw")
        if "network" in pending:
            self._write_network(network, branch_cells)

    @staticmethod
    def _select(
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

    def _write_geodataframe(self, name: str, data: gpd.GeoDataFrame) -> None:
        self.outputs[name].parent.mkdir(parents=True, exist_ok=True)
        data.to_parquet(self.outputs[name], index=False)

    def _write_network(
        self,
        network: StandardNetwork,
        branch_mapping: pd.DataFrame,
    ) -> None:
        directory = self.outputs["network"]
        directory.mkdir(parents=True, exist_ok=True)
        for component in ("bus", "branch", "transformer", "converter"):
            getattr(network, component).to_parquet(
                directory / f"{component}.parquet", index=False
            )
        branch_mapping.to_parquet(directory / "branch_mapping.parquet", index=False)

    def _read_network(self) -> MappedNetwork:
        directory = self.outputs["network"]
        branch_mapping = pd.read_parquet(directory / "branch_mapping.parquet")
        branch_mapping.attrs["mapping_dataset_id"] = "network"
        annotate_schema(branch_mapping, "network", "branch_mapping")
        return MappedNetwork(
            self._read_geodataframe_path(
                directory / "bus.parquet", "network", "bus"
            ),
            self._read_geodataframe_path(
                directory / "branch.parquet", "network", "branch"
            ),
            self._read_geodataframe_path(
                directory / "transformer.parquet", "network", "transformer"
            ),
            self._read_geodataframe_path(
                directory / "converter.parquet", "network", "converter"
            ),
            branch_mapping,
        )

    def _read_geodataframe(
        self,
        name: str,
        dataset_id: str,
        component: str = "data",
    ) -> gpd.GeoDataFrame:
        return self._read_geodataframe_path(
            self.outputs[name], dataset_id, component
        )

    @staticmethod
    def _read_geodataframe_path(
        path: Path,
        dataset_id: str,
        component: str = "data",
    ) -> gpd.GeoDataFrame:
        data = gpd.read_parquet(
            path,
            to_pandas_kwargs={"types_mapper": pd.ArrowDtype},
        )
        data.attrs.update({
            "standard_dataset_id": dataset_id,
            "mapping_dataset_id": dataset_id,
        })
        annotate_schema(data, dataset_id, component)
        return data

    def _output_paths(self, name: str) -> tuple[Path, ...]:
        if name != "network":
            return (self.outputs[name],)
        directory = self.outputs["network"]
        return tuple(
            directory / filename
            for filename in (
                "bus.parquet", "branch.parquet", "transformer.parquet",
                "converter.parquet", "branch_mapping.parquet"
            )
        )

    def _write_xarray(self, name: str, data: xr.Dataset, variable: str) -> None:
        path = self.outputs[name]
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        data.to_netcdf(
            temporary,
            encoding={variable: {"zlib": True, "complevel": 4}},
        )
        temporary.replace(path)

    def _read_xarray(self, name: str, dataset_id: str) -> xr.Dataset:
        data = xr.open_dataset(
            self.outputs[name], chunks="auto", drop_variables=["geometry"]
        )
        if "spatial_uid" not in data.coords:
            data = data.assign_coords(
                spatial_uid=("uid", data["uid"].values.astype(str))
            )
        cells = gpd.read_parquet(
            self.outputs["spatial"], columns=["spatial_uid", "geometry"]
        ).set_index("spatial_uid")
        geometry = cells.geometry.to_wkt().reindex(
            data["spatial_uid"].values.astype(str)
        )
        if geometry.isna().any():
            raise ValueError(f"{dataset_id} contains unknown spatial_uid values.")
        data = data.assign_coords(
            geometry=("uid", geometry.to_numpy(dtype=object)),
            geometry_method=("uid", ["standard_cell"] * data.sizes["uid"])
        )
        data.attrs["mapping_dataset_id"] = dataset_id
        annotate_schema(data, dataset_id)
        return data

    def _read_parameter(self):
        data = as_parameter_data(pd.read_parquet(
            self.outputs["parameter"], dtype_backend="pyarrow"
        ))
        options = self.standard_data.datasets["parameter"].get("options", {})
        data.attrs.update({
            "standard_dataset_id": "parameter",
            "mapping_dataset_id": "parameter",
            "crs": "not_applicable",
            "quality_order": tuple(
                options.get("quality_order", DEFAULT_QUALITY_ORDER)
            ),
            "name_aliases": dict(options.get("name_aliases", {})),
            "conflict_tolerance": float(
                options.get("conflict_tolerance", 1e-6)
            ),
        })
        return data
