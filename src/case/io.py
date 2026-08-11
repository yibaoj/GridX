"""Persistent storage for backend-neutral power-system cases."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
import xarray as xr

from .model import CaseComponent, CaseNetwork, PowerSystemCase


CASE_DATASETS = (
    "spatial", "population", "load", "resource", "network", "generator",
    "storage",
)


def save_case(case: PowerSystemCase, root: Path) -> None:
    """Write one complete reusable case without plot artifacts."""

    root.mkdir(parents=True, exist_ok=True)
    case.spatial.to_parquet(root / "spatial.parquet", index=False)
    case.population.to_parquet(root / "population.parquet", index=False)
    _write_xarray(case.load, root / "load.nc", "demand_mw")
    _write_xarray(case.resource, root / "resource.nc", "availability_pu")
    _write_component(case.generator, root / "generator")
    _write_component(case.storage, root / "storage")
    _write_network(case.network, root / "network")
    case.validation.to_parquet(root / "validation.parquet", index=False)


def load_case(root: Path, config: dict) -> PowerSystemCase:
    """Load a complete case previously written by :func:`save_case`."""

    return PowerSystemCase(
        network=_read_network(root / "network"),
        generator=_read_component(root / "generator"),
        storage=_read_component(root / "storage"),
        load=xr.open_dataset(root / "load.nc", chunks="auto"),
        spatial=gpd.read_parquet(root / "spatial.parquet"),
        resource=xr.open_dataset(root / "resource.nc", chunks="auto"),
        population=gpd.read_parquet(root / "population.parquet"),
        validation=pd.read_parquet(root / "validation.parquet"),
        config=config,
    )


def load_case_dataset(root: Path, dataset_id: str, config: dict) -> object:
    """Load one public case dataset without materializing the whole case."""

    if dataset_id not in CASE_DATASETS:
        raise KeyError(f"Unknown case dataset {dataset_id!r}; expected {CASE_DATASETS}.")
    if dataset_id == "network":
        return _read_network(root / "network")
    if dataset_id in {"generator", "storage"}:
        return _read_component(root / dataset_id)
    if dataset_id in {"load", "resource"}:
        return xr.open_dataset(root / f"{dataset_id}.nc", chunks="auto")
    return gpd.read_parquet(root / f"{dataset_id}.parquet")


def case_outputs(root: Path) -> tuple[Path, ...]:
    """Return every file required to reconstruct a case."""

    paths = [
        root / "spatial.parquet", root / "population.parquet",
        root / "load.nc", root / "resource.nc", root / "validation.parquet",
    ]
    for dataset_id in ("generator", "storage"):
        paths.extend(
            root / dataset_id / name
            for name in ("data.parquet", "parameter.parquet", "membership.parquet")
        )
    paths.append(root / "network" / "branch_mapping.parquet")
    for component in ("bus", "branch", "transformer", "converter"):
        paths.extend([
            root / "network" / f"{component}.parquet",
            root / "network" / f"{component}_parameter.parquet",
            root / "network" / f"{component}_membership.parquet",
        ])
    return tuple(paths)


def _write_component(component: CaseComponent, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    component.data.to_parquet(directory / "data.parquet", index=False)
    component.parameter.to_parquet(directory / "parameter.parquet", index=False)
    component.membership.to_parquet(directory / "membership.parquet", index=False)


def _read_component(directory: Path) -> CaseComponent:
    return CaseComponent(
        gpd.read_parquet(directory / "data.parquet"),
        pd.read_parquet(directory / "parameter.parquet"),
        pd.read_parquet(directory / "membership.parquet"),
    )


def _write_network(network: CaseNetwork, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("bus", "branch", "transformer", "converter"):
        component = getattr(network, name)
        component.data.to_parquet(directory / f"{name}.parquet", index=False)
        component.parameter.to_parquet(
            directory / f"{name}_parameter.parquet", index=False
        )
        component.membership.to_parquet(
            directory / f"{name}_membership.parquet", index=False
        )
    network.branch_mapping.to_parquet(
        directory / "branch_mapping.parquet", index=False
    )


def _read_network(directory: Path) -> CaseNetwork:
    components = {
        name: CaseComponent(
            gpd.read_parquet(directory / f"{name}.parquet"),
            pd.read_parquet(directory / f"{name}_parameter.parquet"),
            pd.read_parquet(directory / f"{name}_membership.parquet"),
        )
        for name in ("bus", "branch", "transformer", "converter")
    }
    return CaseNetwork(
        **components,
        branch_mapping=pd.read_parquet(directory / "branch_mapping.parquet"),
    )


def _write_xarray(data: xr.Dataset, path: Path, variable: str) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    data.to_netcdf(
        temporary,
        encoding={variable: {"zlib": True, "complevel": 4}},
    )
    temporary.replace(path)
