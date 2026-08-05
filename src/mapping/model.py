"""Public mapping-layer result objects."""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import pandas as pd
import xarray as xr


MAPPING_IDS = (
    "spatial",
    "population",
    "load",
    "resource",
    "network",
    "generation",
    "storage",
)


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
class MappingData:
    """Materialized crosswalks and mapped datasets for case construction."""

    spatial: gpd.GeoDataFrame
    population: gpd.GeoDataFrame
    load: xr.Dataset
    resource: xr.Dataset
    network: MappedNetwork
    generation: gpd.GeoDataFrame
    storage: gpd.GeoDataFrame

    @property
    def schema(self) -> object:
        from .schema import SchemaAccessor

        return SchemaAccessor(self)
