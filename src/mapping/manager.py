"""Public manager for the spatiotemporal mapping layer."""

from __future__ import annotations

from pathlib import Path
import tomllib

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
import xarray as xr

from ..standard import NetworkData, StandardDataManager
from ..standard.plot import PlotResult
from .cell import build_spatial_cells
from .model import MAPPING_IDS, MappedNetwork, MappingData
from .network import (
    attach_node_coordinates,
    attach_node_mapping,
    largest_connected_network,
    map_branches_to_cells,
    map_objects_to_cells,
    map_to_nodes,
)
from .plot import PLOTTERS
from .space import aggregate_extensive, map_timeseries_to_cells
from .time import align_time
from .schema import annotate_schema, mapping_schema


class SpatiotemporalMappingManager:
    """Build explicit spatial-cell and electrical-node mappings."""

    _OUTPUTS_BY_MAPPING = {
        "spatial": ("spatial",),
        "population": ("population",),
        "load": ("load", "load_node"),
        "resource": ("resource",),
        "network": (
            "network_nodes", "network_branches", "network_branch_cells"
        ),
        "generation": ("generation", "generation_node"),
        "storage": ("storage", "storage_node"),
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

    def build_network(self) -> NetworkData:
        """Return the largest connected subgraph of the standard network."""

        return largest_connected_network(self.standard_data.load("network"))

    def build(self) -> MappingData:
        """Build all configured cell, time, network, and node mappings."""

        cells = self.build_cells()
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

        auxiliary_cells = None
        auxiliary_value = None
        if load_method == "auxiliary":
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
        elif load_method != "linear":
            raise ValueError(
                "Load downscaling method must be 'linear' or 'auxiliary'."
            )
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
            conservation_tolerance=float(load_options["conservation_tolerance"]),
        )
        self._write_xarray("load", load, "demand_mw")
        load_source.close()
        load = self._read_xarray("load", "load")

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
        resource = self._read_xarray("resource", "resource")

        connected_network = self.build_network()
        network = NetworkData(
            map_objects_to_cells(
                connected_network.nodes,
                cells,
                metric_crs=self.options["metric_crs"],
            ),
            connected_network.branches,
        )
        network.branches.attrs["mapping_dataset_id"] = "network"
        annotate_schema(network.nodes, "network", "nodes")
        annotate_schema(network.branches, "network", "branches")
        self._write_geodataframe("network_nodes", network.nodes)
        self._write_geodataframe("network_branches", network.branches)
        generation = map_objects_to_cells(
            self.standard_data.load("generation"),
            cells,
            metric_crs=self.options["metric_crs"],
        )
        storage = map_objects_to_cells(
            self.standard_data.load("storage"),
            cells,
            metric_crs=self.options["metric_crs"],
        )
        self._write_geodataframe("generation", generation)
        self._write_geodataframe("storage", storage)
        branch_cells = map_branches_to_cells(
            network.branches,
            cells,
            metric_crs=self.options["metric_crs"],
        )
        self._write_dataframe("network_branch_cells", branch_cells)

        asset_options = self.config["asset_node_mapping"]
        node_classes = list(self.config["network"].get("node_classes", []))
        common = {
            "nodes": network.nodes,
            "cells": cells,
            "source_uid_column": "uid",
            "prefer_same_admin": bool(asset_options["prefer_same_admin"]),
            "metric_crs": self.options["metric_crs"],
            "random_seed": int(self.options["random_seed"]),
            "node_classes": node_classes,
        }
        generation_node = map_to_nodes(
            generation,
            output_uid_column="generation_uid",
            method=str(asset_options["generation_method"]),
            **common,
        )
        storage_node = map_to_nodes(
            storage,
            output_uid_column="storage_uid",
            method=str(asset_options["storage_method"]),
            **common,
        )
        load_cells = cells.copy()
        load_cells["geometry"] = load_cells["centre_geometry"]
        load_cells = load_cells.set_geometry("geometry")
        load_node_options = self.config["load_node_mapping"]
        load_node = map_to_nodes(
            load_cells,
            network.nodes,
            cells,
            source_uid_column="spatial_uid",
            output_uid_column="load_spatial_uid",
            method=str(load_node_options["method"]),
            prefer_same_admin=bool(load_node_options["prefer_same_admin"]),
            metric_crs=self.options["metric_crs"],
            random_seed=int(self.options["random_seed"]),
            node_classes=node_classes,
        )
        self._write_dataframe("generation_node", generation_node)
        self._write_dataframe("storage_node", storage_node)
        self._write_dataframe("load_node", load_node)
        return MappingData(
            spatial=cells,
            population=population,
            load=annotate_schema(
                attach_node_coordinates(
                    load,
                    load_node,
                    source_uid_column="load_spatial_uid",
                ),
                "load",
            ),
            resource=annotate_schema(resource, "resource"),
            network=MappedNetwork(
                network.nodes,
                network.branches,
                branch_cells,
            ),
            generation=annotate_schema(
                attach_node_mapping(
                    generation,
                    generation_node,
                    source_uid_column="generation_uid",
                ),
                "generation",
            ),
            storage=annotate_schema(
                attach_node_mapping(
                    storage,
                    storage_node,
                    source_uid_column="storage_uid",
                ),
                "storage",
            ),
        )

    def load(self, mapping_id: str | None = None) -> object:
        """Load one mapped product, or all products as MappingData."""

        mapping_ids = MAPPING_IDS
        if mapping_id is None:
            return MappingData(**{
                name: self.load(name)
                for name in mapping_ids
            })
        if mapping_id not in mapping_ids:
            raise KeyError(
                f"Unknown mapping_id {mapping_id!r}; expected one of {mapping_ids}."
            )
        required = self._OUTPUTS_BY_MAPPING[mapping_id]
        missing = [name for name in required if not self.outputs[name].exists()]
        if missing:
            raise FileNotFoundError(f"Mapping outputs are unavailable: {missing}")
        if mapping_id == "network":
            return MappedNetwork(
                self._read_geodataframe("network_nodes", "network", "nodes"),
                self._read_geodataframe(
                    "network_branches", "network", "branches"
                ),
                pd.read_parquet(self.outputs["network_branch_cells"]),
            )
        if mapping_id in {"spatial", "population"}:
            return self._read_geodataframe(mapping_id, mapping_id)
        if mapping_id == "resource":
            return self._read_xarray("resource", "resource")
        if mapping_id == "load":
            data = self._read_xarray("load", "load")
            return annotate_schema(
                attach_node_coordinates(
                    data,
                    pd.read_parquet(self.outputs["load_node"]),
                    source_uid_column="load_spatial_uid",
                ),
                "load",
            )
        return annotate_schema(
            attach_node_mapping(
                self._read_geodataframe(mapping_id, mapping_id),
                pd.read_parquet(self.outputs[f"{mapping_id}_node"]),
                source_uid_column=f"{mapping_id}_uid",
            ),
            mapping_id,
        )

    def check(self) -> pd.Series:
        """Report whether each configured mapping output exists."""

        return pd.Series(
            {
                mapping_id: all(self.outputs[name].exists() for name in names)
                for mapping_id, names in self._OUTPUTS_BY_MAPPING.items()
            },
            name="output_available",
        )

    def plot(self, mapping_id: str, **kwargs: object) -> PlotResult:
        """Return one mapped-data figure without writing an output file."""

        data = self.load(mapping_id)
        kwargs.setdefault("spatial", self.standard_data.load("spatial"))
        kwargs.setdefault("map_crs", self.options["metric_crs"])
        if mapping_id in {"network", "generation", "storage"}:
            kwargs.setdefault("cells", self.load("spatial"))
        with plt.ioff():
            figure = PLOTTERS[mapping_id](data, **kwargs)
        if isinstance(data, xr.Dataset):
            data.close()
        return figure

    def schema(
        self,
        mapping_ids: str | list[str] | tuple[str, ...] | None = None,
    ) -> pd.DataFrame:
        """Describe actual structures in one, several, or all mapped products."""

        selected = self._select(mapping_ids)
        available = self.check().reindex(selected)
        if mapping_ids is not None and not available.all():
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
            else pd.DataFrame(columns=("mapping_id",))
        )
        result.attrs["unavailable_mapping_ids"] = tuple(
            available.index[~available]
        )
        return result

    @staticmethod
    def _select(
        mapping_ids: str | list[str] | tuple[str, ...] | None,
    ) -> list[str]:
        selected = (
            list(MAPPING_IDS)
            if mapping_ids is None
            else [mapping_ids]
            if isinstance(mapping_ids, str)
            else list(mapping_ids)
        )
        unknown = set(selected).difference(MAPPING_IDS)
        if unknown:
            raise KeyError(f"Unknown mapping_id values: {sorted(unknown)}")
        return selected

    def _write_geodataframe(self, name: str, data: gpd.GeoDataFrame) -> None:
        self.outputs[name].parent.mkdir(parents=True, exist_ok=True)
        data.to_parquet(self.outputs[name], index=False)

    def _read_geodataframe(
        self,
        name: str,
        mapping_id: str,
        component: str = "data",
    ) -> gpd.GeoDataFrame:
        data = gpd.read_parquet(
            self.outputs[name],
            to_pandas_kwargs={"types_mapper": pd.ArrowDtype},
        )
        data.attrs.update({
            "standard_dataset_id": mapping_id,
            "mapping_dataset_id": mapping_id,
        })
        annotate_schema(data, mapping_id, component)
        return data

    def _write_dataframe(self, name: str, data: pd.DataFrame) -> None:
        self.outputs[name].parent.mkdir(parents=True, exist_ok=True)
        data.to_parquet(self.outputs[name], index=False)

    def _write_xarray(self, name: str, data: xr.Dataset, variable: str) -> None:
        path = self.outputs[name]
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        data.to_netcdf(
            temporary,
            encoding={variable: {"zlib": True, "complevel": 4}},
        )
        temporary.replace(path)

    def _read_xarray(self, name: str, mapping_id: str) -> xr.Dataset:
        data = xr.open_dataset(self.outputs[name], chunks="auto")
        if "spatial_uid" not in data.coords:
            data = data.assign_coords(
                spatial_uid=("uid", data["uid"].values.astype(str))
            )
        data = data.assign_coords(
            geometry_method=("uid", ["standard_cell"] * data.sizes["uid"])
        )
        data.attrs["mapping_dataset_id"] = mapping_id
        annotate_schema(data, mapping_id)
        return data
