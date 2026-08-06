"""Public case-layer construction manager."""

from __future__ import annotations

from pathlib import Path
import tomllib

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd

from ..mapping import MappingData, SpatiotemporalMappingManager
from ..standard import NetworkData, StandardDataManager
from .aggregate import aggregate_assets, aggregate_load
from .backends.manifest import load_pypsa_manifest
from .model import CaseComponent, CaseNetwork, PowerSystemCase
from .network import filter_network
from .parameters import resolve_parameters
from .remap import remap_assets, remap_load
from .time import common_time, select_time


class PowerSystemCaseManager:
    """Construct a backend-neutral case from mapped and standard data."""

    def __init__(
        self,
        config_path: str | Path = "config/case.toml",
        mapped_data: MappingData | SpatiotemporalMappingManager | None = None,
        standard_data: StandardDataManager | None = None,
    ) -> None:
        path = Path(config_path).expanduser()
        if not path.is_absolute() and not path.exists():
            path = Path(__file__).resolve().parents[2] / path
        with path.resolve().open("rb") as file:
            self.config = tomllib.load(file)
        if standard_data is None and isinstance(
            mapped_data, SpatiotemporalMappingManager
        ):
            standard_data = mapped_data.standard_data
        self.standard_data = standard_data or StandardDataManager(
            path.resolve().parents[1] / "config/standard_data.toml"
        )
        self.mapped_data = (
            mapped_data.load() if isinstance(mapped_data, SpatiotemporalMappingManager)
            else mapped_data
            if mapped_data is not None
            else SpatiotemporalMappingManager(
                path.resolve().parents[1] / "config/mapping.toml",
                self.standard_data,
            ).load()
        )

    def build(self) -> PowerSystemCase:
        """Build, validate, and return the configured case."""

        options = self.config
        general = options["general"]
        network_data, branch_mapping = filter_network(
            self.mapped_data.network, options["network"]
        )
        cells = self.mapped_data.spatial
        bus_subclasses = list(options["network"].get("bus_subclasses", []))
        common = {
            "buses": network_data.bus,
            "cells": cells,
            "metric_crs": str(general["metric_crs"]),
            "random_seed": int(general["random_seed"]),
            "bus_subclasses": bus_subclasses,
        }

        generator = remap_assets(
            _filter_assets(
                self.mapped_data.generator,
                options["assets"]["generator"],
                "capacity_mw",
            ),
            dataset_id="generator",
            options=options["asset_bus_mapping"]["generator"],
            **common,
        )
        storage = remap_assets(
            _filter_assets(
                self.mapped_data.storage,
                options["assets"]["storage"],
                "power_capacity_mw",
            ),
            dataset_id="storage",
            options=options["asset_bus_mapping"]["storage"],
            **common,
        )
        load = remap_load(
            select_time(self.mapped_data.load, options["time"]),
            options=options["load_bus_mapping"],
            **common,
        )
        resource = select_time(self.mapped_data.resource, options["time"])
        load, resource = common_time(load, resource)
        load_cells = load

        parameter_source = self.standard_data.load("parameter")
        at = options["parameters"].get("at")
        parameters = {
            "bus": resolve_parameters(
                network_data.bus, parameter_source,
                dataset_id="network", component="bus", at=at,
            ),
            "branch": resolve_parameters(
                network_data.branch, parameter_source,
                dataset_id="network", component="branch", at=at,
            ),
            "transformer": resolve_parameters(
                network_data.transformer, parameter_source,
                dataset_id="network", component="transformer", at=at,
            ),
            "converter": resolve_parameters(
                network_data.converter, parameter_source,
                dataset_id="network", component="converter", at=at,
            ),
            "generator": resolve_parameters(
                generator, parameter_source,
                dataset_id="generator", component="generator", at=at,
            ),
            "storage": resolve_parameters(
                storage, parameter_source,
                dataset_id="storage", component="storage", at=at,
            ),
        }

        aggregation = options["aggregation"]
        aggregate_options = {
            "buses": network_data.bus,
            "cells": cells,
            "sum_names": list(aggregation.get("sum_parameter_names", [])),
            "boolean_names": list(aggregation.get("boolean_parameter_names", [])),
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
            network_data,
            generator,
            storage,
            load,
            load_cells,
            resource,
            parameters,
            load_pypsa_manifest(
                options["backend"]["pypsa"]["parameter_manifest"]
            ),
            generator_membership,
            dict(options["backend"]["pypsa"].get("resource_class_mapping", {})),
        )
        return PowerSystemCase(
            network=network,
            generator=CaseComponent(
                generator, parameters["generator"], generator_membership
            ),
            storage=CaseComponent(
                storage, parameters["storage"], storage_membership
            ),
            load=load,
            spatial=self.mapped_data.spatial,
            resource=resource,
            population=self.mapped_data.population,
            validation=validation,
            config=self.config,
        )


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


def _component(data: gpd.GeoDataFrame, parameter: pd.DataFrame) -> CaseComponent:
    membership = pd.DataFrame({
        "source_uid": data["uid"], "aggregate_uid": data["uid"], "weight": 1.0
    })
    return CaseComponent(data, parameter, membership)


def _validate_case(
    network: NetworkData,
    generator: gpd.GeoDataFrame,
    storage: gpd.GeoDataFrame,
    load,
    load_cells,
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
        "status": "pass" if load.time.equals(resource.time) else "fail",
        "value": load.sizes["time"],
        "detail": f"{load.attrs.get('time_step')} in {load.attrs.get('timezone')}",
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
    load_coverage = _validate_load_profiles(load, load_cells, network.bus)
    return pd.concat(
        [pd.DataFrame(rows), coverage, resource_coverage, load_coverage],
        ignore_index=True,
    )


def _validate_load_profiles(load, load_cells, buses: pd.DataFrame) -> pd.DataFrame:
    """Check electrical mapping, values, profiles, conservation, and marine load."""

    variable = "demand_mw"
    values = np.asarray(load[variable].values, dtype=float)
    cell_values = np.asarray(load_cells[variable].values, dtype=float)
    load_uids = load["uid"].values.astype(str)
    bus_uids = set(buses["uid"].astype(str))
    missing_buses = sorted(set(load_uids).difference(bus_uids))
    finite = np.isfinite(values)
    all_nan = np.isnan(values).all(axis=(0, 2))
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

    levels = load_cells.coords.get("spatial_level")
    marine = (
        levels.values.astype(str) == "marine_zone"
        if levels is not None else np.zeros(load_cells.sizes["uid"], dtype=bool)
    )
    marine_values = cell_values[:, marine, :]
    marine_valid = (
        np.isfinite(marine_values).all()
        and np.isclose(marine_values, 0.0, atol=1e-9).all()
    ) if marine.any() else True
    marine_nonzero = int(
        np.count_nonzero(~np.isclose(marine_values, 0.0, atol=1e-9))
    ) if marine.any() else 0

    profile_status = "fail" if all_nan.any() else "warning" if all_zero.any() else "pass"
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
            "status": "pass" if finite.all() else "fail",
            "value": float(finite.mean()) if finite.size else 1.0,
            "detail": f"{int((~finite).sum())}/{finite.size} values are NaN or infinite",
        }, {
            "check": "load_profile_coverage", "component": "load",
            "name": "profile_quality",
            "status": profile_status,
            "value": float((~all_nan & ~all_zero).mean()) if len(load_uids) else 1.0,
            "detail": f"all_nan={int(all_nan.sum())}; all_zero={int(all_zero.sum())}",
        }, {
            "check": "load_profile_coverage", "component": "load",
            "name": "aggregate_conservation",
            "status": "pass" if conserved else "fail",
            "value": max_error,
            "detail": "Maximum absolute source-to-bus load error in MW",
        }, {
            "check": "load_profile_coverage", "component": "load",
            "name": "marine_zero_load",
            "status": "not_applicable" if not marine.any()
            else "pass" if marine_valid else "fail",
            "value": marine_nonzero,
            "detail": f"{int(marine.sum())} marine cells; {marine_nonzero} nonzero or non-finite values",
        },
    ])
