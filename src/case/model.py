"""Backend-neutral power-system case objects."""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import pandas as pd
import xarray as xr


@dataclass(frozen=True)
class CaseComponent:
    """One case component and its resolved parameter long table."""

    data: gpd.GeoDataFrame
    parameter: pd.DataFrame
    membership: pd.DataFrame


@dataclass(frozen=True)
class CaseNetwork:
    """Filtered connected network and component-level parameters."""

    bus: CaseComponent
    branch: CaseComponent
    transformer: CaseComponent
    converter: CaseComponent
    branch_mapping: pd.DataFrame

    @property
    def parameter(self) -> pd.DataFrame:
        tables = [
            component.parameter
            for component in (
                self.bus, self.branch, self.transformer, self.converter
            )
            if not component.parameter.empty
        ]
        return pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()


@dataclass(frozen=True)
class PowerSystemCase:
    """Solver-independent data prepared for planning and operation models."""

    network: CaseNetwork
    generator: CaseComponent
    storage: CaseComponent
    load: xr.Dataset
    spatial: gpd.GeoDataFrame
    resource: xr.Dataset
    population: gpd.GeoDataFrame
    validation: pd.DataFrame
    config: dict

    def plot(self, dataset_id: str, **kwargs: object) -> object:
        """Return one case-layer map without displaying or saving it."""

        from ..visualization import plot

        return plot(self, dataset_id, **kwargs)

    def close(self) -> None:
        """Close lazy time-series stores owned by this case."""

        self.load.close()
        self.resource.close()

    def __enter__(self) -> "PowerSystemCase":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def to_pypsa(self, *, strict: bool | None = None):
        """Convert this case with the optional PyPSA backend."""

        from .backends.pypsa import to_pypsa

        return to_pypsa(self, strict=strict)

    def parameter_manifest(self, backend: str = "pypsa") -> pd.DataFrame:
        """Return the backend parameter contract used by validation and conversion."""

        if backend != "pypsa":
            raise KeyError(f"Unsupported backend {backend!r}.")
        from .backends.manifest import load_pypsa_manifest

        path = self.config["backend"][backend]["parameter_manifest"]
        return load_pypsa_manifest(path).table()
