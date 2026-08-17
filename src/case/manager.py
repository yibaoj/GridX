"""Public case-layer construction manager."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import tomllib

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd

from ..mapping import MappedData, SpatiotemporalMappingManager
from ..standard import StandardNetwork
from .aggregate import aggregate_assets, aggregate_load
from .backends.manifest import load_pypsa_manifest
from .io import CASE_DATASETS, case_outputs, load_case, load_case_dataset, save_case
from .model import CaseComponent, CaseNetwork, PowerSystemCase
from .network import filter_network
from .parameter import resolve_parameters
from .remap import remap_assets, remap_load
from .time import select_time


class PowerSystemCaseManager:
    """Construct a backend-neutral case from one mapped-data snapshot."""

    def __init__(
        self,
        config_path: str | Path = "config/case.toml",
        mapped_data: MappedData | SpatiotemporalMappingManager | None = None,
    ) -> None:
        path = Path(config_path).expanduser()
        if not path.is_absolute() and not path.exists():
            path = Path(__file__).resolve().parents[2] / path
        with path.resolve().open("rb") as file:
            self.config = tomllib.load(file)
        self.output_root = path.resolve().parents[1] / self.config["general"]["output_root"]
        if isinstance(mapped_data, SpatiotemporalMappingManager):
            self.mapping_manager = mapped_data
            self.mapped_data = None
        elif mapped_data is None:
            self.mapping_manager = SpatiotemporalMappingManager(
                path.resolve().parents[1] / "config/mapping.toml"
            )
            self.mapped_data = None
        else:
            self.mapping_manager = None
            self.mapped_data = mapped_data
        self._case_cache: PowerSystemCase | None = None

    def build(
        self,
        dataset_ids: str | Iterable[str] | None = None,
        *,
        overwrite: bool = False,
    ) -> pd.DataFrame:
        """Build a missing coherent case and return its final check report."""

        selected = self._select(dataset_ids)
        complete = bool(self.check()["case_complete"].iat[0])
        initial = self.check(selected)
        if (
            (overwrite or not complete)
            and bool(initial["inputs_available"].iat[0])
        ):
            self.close()
            self._build_case()
        return self.check(selected)

    def load(self, dataset_id: str | None = None) -> object:
        """Load one case dataset, or the complete reusable case."""

        paths = case_outputs(self.output_root)
        if dataset_id is not None:
            if dataset_id not in CASE_DATASETS:
                raise KeyError(
                    f"Unknown case dataset {dataset_id!r}; expected {CASE_DATASETS}."
                )
            paths = tuple(
                path for path in paths
                if _case_path_dataset(path, self.output_root) == dataset_id
            )
        missing = [path for path in paths if not path.exists()]
        if missing:
            raise FileNotFoundError(
                "Case outputs are incomplete; run build() first. Missing: "
                f"{[str(path) for path in missing[:5]]}"
            )
        if dataset_id is None:
            if self._case_cache is None:
                self._case_cache = load_case(self.output_root, self.config)
            return self._case_cache
        return load_case_dataset(self.output_root, dataset_id, self.config)

    def check(
        self,
        dataset_ids: str | Iterable[str] | None = None,
    ) -> pd.DataFrame:
        """Report whether every public case dataset is available."""

        selected = self._select(dataset_ids)
        paths = case_outputs(self.output_root)
        case_complete = all(path.exists() for path in paths)
        mapped_inputs_available = (
            self.mapped_data is not None
            or bool(self.mapping_manager.check()["available"].all())
        )
        inputs_available = bool(mapped_inputs_available)
        rows = []
        for dataset_id in selected:
            available = all(
                path.exists() for path in paths
                if _case_path_dataset(path, self.output_root) == dataset_id
            )
            rows.append({
                "dataset_id": dataset_id,
                "inputs_available": inputs_available,
                "available": available,
                "case_complete": case_complete,
                "status": (
                    "available"
                    if available and case_complete
                    else "incomplete_case"
                    if available
                    else "ready_to_build"
                    if inputs_available
                    else "input_unavailable"
                ),
            })
        return pd.DataFrame(rows).set_index("dataset_id")

    def plot(self, dataset_id: str, **kwargs: object):
        """Return a case figure without displaying or saving it."""

        return self.load().plot(dataset_id, **kwargs)

    def close(self) -> None:
        """Close and release the cached complete case, if loaded."""

        case = getattr(self, "_case_cache", None)
        if case is not None:
            case.close()
            self._case_cache = None

    @staticmethod
    def _select(
        dataset_ids: str | Iterable[str] | None,
    ) -> list[str]:
        selected = (
            list(CASE_DATASETS)
            if dataset_ids is None
            else [dataset_ids]
            if isinstance(dataset_ids, str)
            else list(dataset_ids)
        )
        unknown = set(selected).difference(CASE_DATASETS)
        if unknown:
            raise KeyError(f"Unknown case dataset IDs: {sorted(unknown)}")
        return selected

    def _build_case(self) -> None:
        """Construct and persist one internally consistent case bundle."""

        options = self.config
        mapped_data = (
            self.mapped_data
            if self.mapped_data is not None
            else self.mapping_manager.load()
        )
        network_data, branch_mapping = filter_network(
            mapped_data.network, options["network"]
        )
        cells = mapped_data.spatial
        common = {
            "buses": network_data.bus,
            "cells": cells,
            "metric_crs": str(options["general"]["metric_crs"]),
            "random_seed": int(options["general"]["random_seed"]),
            "bus_subclasses": list(
                options["network"].get("bus_subclasses", [])
            ),
        }
        generator = remap_assets(
            _filter_assets(
                mapped_data.generator, options["assets"]["generator"],
                "capacity_mw",
            ),
            dataset_id="generator",
            options=options["asset_bus_mapping"]["generator"],
            **common,
        )
        storage = remap_assets(
            _filter_assets(
                mapped_data.storage, options["assets"]["storage"],
                "power_capacity_mw",
            ),
            dataset_id="storage",
            options=options["asset_bus_mapping"]["storage"],
            **common,
        )
        load = remap_load(
            select_time(mapped_data.load, options["time"]),
            options=options["load_bus_mapping"],
            **common,
        )
        resource = mapped_data.resource
        load_cells = load
        at = options["parameters"].get("at")
        parameters = {
            component: resolve_parameters(
                getattr(network_data, component), mapped_data.parameter,
                dataset_id="network", component=component, at=at,
            )
            for component in ("bus", "branch", "transformer", "converter")
        }
        parameters.update({
            "generator": resolve_parameters(
                generator, mapped_data.parameter,
                dataset_id="generator", component="generator", at=at,
            ),
            "storage": resolve_parameters(
                storage, mapped_data.parameter,
                dataset_id="storage", component="storage", at=at,
            ),
        })
        aggregation = options["aggregation"]
        aggregate_options = {
            "buses": network_data.bus,
            "cells": cells,
            "sum_names": list(aggregation.get("sum_parameter_names", [])),
            "boolean_names": list(
                aggregation.get("boolean_parameter_names", [])
            ),
        }
        generator, parameters["generator"], generator_membership = aggregate_assets(
            generator, parameters["generator"], dataset_id="generator",
            method=str(aggregation["generator"]), **aggregate_options,
        )
        storage, parameters["storage"], storage_membership = aggregate_assets(
            storage, parameters["storage"], dataset_id="storage",
            method=str(aggregation["storage"]), **aggregate_options,
        )
        if aggregation["generator"] == "cell":
            generator = remap_assets(
                generator, dataset_id="generator",
                options=options["asset_bus_mapping"]["generator"], **common,
            )
        if aggregation["storage"] == "cell":
            storage = remap_assets(
                storage, dataset_id="storage",
                options=options["asset_bus_mapping"]["storage"], **common,
            )
        load = aggregate_load(load, str(aggregation["load"]))
        network = CaseNetwork(
            bus=_component(network_data.bus, parameters["bus"]),
            branch=_component(network_data.branch, parameters["branch"]),
            transformer=_component(
                network_data.transformer, parameters["transformer"]
            ),
            converter=_component(network_data.converter, parameters["converter"]),
            branch_mapping=branch_mapping,
        )
        validation = _validate_case(
            network_data, generator, storage, load, load_cells,
            mapped_data.population, resource, parameters,
            load_pypsa_manifest(
                options["backend"]["pypsa"]["parameter_manifest"]
            ),
            generator_membership,
            dict(options["backend"]["pypsa"].get("resource_class_mapping", {})),
        )
        save_case(PowerSystemCase(
            network=network,
            generator=CaseComponent(
                generator, parameters["generator"], generator_membership
            ),
            storage=CaseComponent(
                storage, parameters["storage"], storage_membership
            ),
            load=load,
            spatial=mapped_data.spatial,
            resource=resource,
            population=mapped_data.population,
            validation=validation,
            config=self.config,
        ), self.output_root)


def _filter_assets(
    data: gpd.GeoDataFrame,
    options: dict,
    capacity_column: str,
) -> gpd.GeoDataFrame:
    status = data["status"].astype("string").str.lower()
    allowed = {str(value).lower() for value in options["statuses"]}
    capacity = pd.to_numeric(data[capacity_column], errors="coerce")
    return data.loc[
        status.isin(allowed)
        & capacity.between(
            float(options["minimum_capacity_mw"]),
            float(options["maximum_capacity_mw"]),
            inclusive="both",
        )
    ].copy()


def _case_path_dataset(path: Path, root: Path) -> str:
    relative = path.relative_to(root)
    return relative.parts[0].split(".", 1)[0]


def _component(data: gpd.GeoDataFrame, parameter: pd.DataFrame) -> CaseComponent:
    membership = pd.DataFrame({
        "source_uid": data["uid"], "aggregate_uid": data["uid"], "weight": 1.0
    })
    return CaseComponent(data, parameter, membership)


def _validate_case(
    network: StandardNetwork,
    generator: gpd.GeoDataFrame,
    storage: gpd.GeoDataFrame,
    load,
    load_cells,
    population: gpd.GeoDataFrame,
    resource,
    parameters: dict[str, pd.DataFrame],
    parameter_manifest,
    generator_membership: pd.DataFrame,
    resource_class_mapping: dict[str, str],
) -> pd.DataFrame:
    graph = nx.Graph()
    graph.add_nodes_from(network.bus["uid"])
    for frame in (network.branch, network.transformer, network.converter):
        graph.add_edges_from(frame[["from_bus_uid", "to_bus_uid"]].itertuples(
            index=False, name=None
        ))
    rows = [{
        "check": "network_connectivity", "component": "network",
        "name": "largest_connected_graph",
        "status": "pass" if nx.is_connected(graph) else "fail",
        "value": nx.number_connected_components(graph),
        "detail": f"{len(network.bus)} buses and {graph.number_of_edges()} connections",
    }, {
        "check": "time_alignment", "component": "timeseries",
        "name": "common_timestamps",
        "status": (
            "pass"
            if pd.Index(load.time.values).isin(resource.time.values).all()
            else "fail"
        ),
        "value": load.sizes["time"],
        "detail": (
            f"{load.sizes['time']} load timestamps are covered by the "
            f"{resource.sizes['time']}-timestamp resource series"
        ),
    }, {
        "check": "aggregation_limit", "component": "generator",
        "name": "clustered_unit_commitment",
        "status": "warning",
        "value": len(generator),
        "detail": (
            "Class/subclass aggregation supports continuous ED/planning and "
            "clustered UC, not exact unit-level binary commitment."
        ),
    }]
    assets = {
        "bus": network.bus, "branch": network.branch,
        "transformer": network.transformer, "converter": network.converter,
        "generator": generator, "storage": storage,
    }
    coverage = parameter_manifest.validate(parameters, assets)
    variable = generator.loc[
        generator["class"].isin(["wind", "solar", "hydropower"])
    ]
    resource_classes = set(resource["class"].values.astype(str))
    resource_uids = set(resource["uid"].values.astype(str))
    covered = 0
    for asset in variable.itertuples(index=False):
        resource_class = resource_class_mapping.get(str(asset.subclass))
        members = generator_membership.loc[
            generator_membership["aggregate_uid"].eq(asset.uid)
        ]
        if (
            resource_class in resource_classes
            and members["source_spatial_uid"].astype(str).isin(resource_uids).any()
        ):
            covered += 1
    resource_coverage = pd.DataFrame([{
        "check": "resource_profile_coverage", "component": "generator",
        "name": "availability_pu",
        "status": "pass" if covered == len(variable) else "fail",
        "value": covered / len(variable) if len(variable) else 1.0,
        "detail": f"{covered}/{len(variable)} variable generators have a mapped profile",
    }])
    load_coverage = _validate_load_profiles(
        load, load_cells, network.bus, population
    )
    return pd.concat(
        [pd.DataFrame(rows), coverage, resource_coverage, load_coverage],
        ignore_index=True,
    )


def _validate_load_profiles(
    load,
    load_cells,
    buses: pd.DataFrame,
    population: pd.DataFrame,
) -> pd.DataFrame:
    """Check electrical mapping, values, profiles, conservation, and marine load."""

    variable = "demand_mw"
    values = np.asarray(load[variable].values, dtype=float)
    cell_values = np.asarray(load_cells[variable].values, dtype=float)
    load_uids = load["uid"].values.astype(str)
    bus_uids = set(buses["uid"].astype(str))
    missing_buses = sorted(set(load_uids).difference(bus_uids))
    finite = np.isfinite(values)
    cell_finite = np.isfinite(cell_values)
    all_nan = np.isnan(values).all(axis=(0, 2))
    cell_all_nan = np.isnan(cell_values).all(axis=(0, 2))
    all_zero = finite.all(axis=(0, 2)) & np.isclose(values, 0.0).all(axis=(0, 2))

    source_total = np.asarray(
        load_cells[variable].sum("uid", skipna=False).values, dtype=float
    )
    target_total = np.asarray(
        load[variable].sum("uid", skipna=False).values, dtype=float
    )
    totals_finite = np.isfinite(source_total).all() and np.isfinite(target_total).all()
    conserved = totals_finite and np.allclose(
        source_total, target_total, rtol=1e-6, atol=1e-6
    )
    max_error = (
        float(np.max(np.abs(source_total - target_total)))
        if totals_finite and source_total.size else np.nan
    )

    auxiliary = population.set_index("spatial_uid")["population"]
    cell_auxiliary = pd.Series(
        load_cells["uid"].values.astype(str)
    ).map(auxiliary)
    zero_auxiliary = cell_auxiliary.eq(0).to_numpy()
    zero_values = cell_values[:, zero_auxiliary, :]
    zero_valid = (
        np.isfinite(zero_values).all()
        and np.isclose(zero_values, 0.0, atol=1e-9).all()
    ) if zero_auxiliary.any() else True
    zero_violations = int(
        np.count_nonzero(~np.isclose(zero_values, 0.0, atol=1e-9))
    ) if zero_auxiliary.any() else 0

    profile_status = (
        "fail" if cell_all_nan.any() or all_nan.any()
        else "warning" if all_zero.any() else "pass"
    )
    return pd.DataFrame([
        {
            "check": "load_profile_coverage", "component": "load",
            "name": "electrical_bus_mapping",
            "status": "pass" if not missing_buses else "fail",
            "value": (len(load_uids) - len(missing_buses)) / len(load_uids)
            if len(load_uids) else 1.0,
            "detail": f"{len(missing_buses)} load UIDs do not reference retained buses",
        }, {
            "check": "load_profile_coverage", "component": "load",
            "name": "finite_values",
            "status": "pass" if finite.all() and cell_finite.all() else "fail",
            "value": float(cell_finite.mean()) if cell_finite.size else 1.0,
            "detail": (
                f"cell_nonfinite={int((~cell_finite).sum())}; "
                f"bus_nonfinite={int((~finite).sum())}"
            ),
        }, {
            "check": "load_profile_coverage", "component": "load",
            "name": "profile_quality",
            "status": profile_status,
            "value": float((~all_nan & ~all_zero).mean()) if len(load_uids) else 1.0,
            "detail": (
                f"cell_all_nan={int(cell_all_nan.sum())}; "
                f"bus_all_nan={int(all_nan.sum())}; "
                f"bus_all_zero={int(all_zero.sum())}"
            ),
        }, {
            "check": "load_profile_coverage", "component": "load",
            "name": "aggregate_conservation",
            "status": "pass" if conserved else "fail",
            "value": max_error,
            "detail": "Maximum absolute source-to-bus load error in MW",
        }, {
            "check": "load_profile_coverage", "component": "load",
            "name": "zero_auxiliary_load",
            "status": "not_applicable" if not zero_auxiliary.any()
            else "pass" if zero_valid else "fail",
            "value": zero_violations,
            "detail": (
                f"{int(zero_auxiliary.sum())} zero-population cells; "
                f"{zero_violations} nonzero or non-finite values"
            ),
        },
    ])
