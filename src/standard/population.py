"""Population-raster standardization."""

from __future__ import annotations

import numpy as np
import rasterio
from rasterio.transform import xy
import xarray as xr

from .base import _Standardizer


class _PopulationStandardizer(_Standardizer):
    def build(self) -> xr.Dataset:
        source_id = self.config["source_ids"][0]
        with rasterio.open(self.source(source_id)) as raster:
            population = raster.read(1, masked=True).filled(np.nan).astype("float32")
            x_coordinates, _ = xy(
                raster.transform,
                np.zeros(raster.width, dtype=int),
                np.arange(raster.width),
            )
            _, y_coordinates = xy(
                raster.transform,
                np.arange(raster.height),
                np.zeros(raster.height, dtype=int),
            )
            dataset = xr.Dataset(
                {"population": (("y", "x"), population)},
                coords={
                    "x": np.asarray(x_coordinates),
                    "y": np.asarray(y_coordinates),
                },
                attrs={
                    "source_id": source_id,
                    "crs": raster.crs.to_wkt(),
                    "transform": tuple(raster.transform),
                    "unit": "persons per source cell",
                },
            )
        path = self.output()
        path.parent.mkdir(parents=True, exist_ok=True)
        dataset.to_netcdf(
            path,
            encoding={"population": {"zlib": True, "complevel": 4}},
        )
        return dataset
