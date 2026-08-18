"""Public standard-layer data objects."""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import xarray as xr

from .parameter import ParameterData


@dataclass(frozen=True)
class StandardNetwork:
    """Canonical electrical network represented by four component tables."""

    bus: gpd.GeoDataFrame
    branch: gpd.GeoDataFrame
    transformer: gpd.GeoDataFrame
    converter: gpd.GeoDataFrame

    @property
    def schema(self) -> object:
        from .schema import _SchemaAccessor

        return _SchemaAccessor(self)


@dataclass(frozen=True)
class StandardData:
    """Complete standard-data snapshot with the configuration that built it."""

    spatial: gpd.GeoDataFrame
    network: StandardNetwork
    generator: gpd.GeoDataFrame
    storage: gpd.GeoDataFrame
    parameter: ParameterData
    load: xr.Dataset
    population: gpd.GeoDataFrame
    resource: xr.Dataset
    config: dict

    @property
    def schema(self) -> object:
        from .schema import _SchemaAccessor

        return _SchemaAccessor(self)

    def plot(self, dataset_id: str, **kwargs: object) -> object:
        """Plot one contained dataset through the shared plotting facade."""

        from ..visualization import plot

        return plot(self, dataset_id, **kwargs)

    def close(self) -> None:
        """Close lazy time-series stores owned by this snapshot."""

        self.load.close()
        self.resource.close()

    def __enter__(self) -> "StandardData":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
