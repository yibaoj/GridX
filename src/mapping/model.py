"""Public mapping-layer result objects."""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import pandas as pd
import xarray as xr

from ..standard import ParameterData


@dataclass(frozen=True)
class MappedNetwork:
    """Largest connected network and its spatial-cell relationships."""

    bus: gpd.GeoDataFrame
    branch: gpd.GeoDataFrame
    transformer: gpd.GeoDataFrame
    converter: gpd.GeoDataFrame
    branch_mapping: pd.DataFrame

    @property
    def schema(self) -> object:
        from .schema import SchemaAccessor

        return SchemaAccessor(self)


@dataclass(frozen=True)
class MappedData:
    """Materialized crosswalks and mapped datasets for case construction."""

    spatial: gpd.GeoDataFrame
    network: MappedNetwork
    generator: gpd.GeoDataFrame
    storage: gpd.GeoDataFrame
    parameter: ParameterData
    load: xr.Dataset
    population: gpd.GeoDataFrame
    resource: xr.Dataset
    config: dict

    @property
    def schema(self) -> object:
        from .schema import SchemaAccessor

        return SchemaAccessor(self)

    def plot(self, dataset_id: str, **kwargs: object) -> object:
        """Plot one contained dataset through the shared plotting facade."""

        from ..visualization import plot

        return plot(self, dataset_id, **kwargs)

    def close(self) -> None:
        """Close lazy time-series stores owned by this snapshot."""

        self.load.close()
        self.resource.close()

    def __enter__(self) -> "MappedData":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
