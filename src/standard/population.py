"""Population-grid standardization."""

from __future__ import annotations

import math

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import Window, bounds
from shapely.geometry import box

from .base import _Standardizer
from .schema import _finalize_frame, _write_geodataframe


class _PopulationStandardizer(_Standardizer):
    def build(self) -> gpd.GeoDataFrame:
        source_id = self.config["source_ids"][0]
        factor = int(self.options.get("aggregation_factor", 1))
        if factor < 1:
            raise ValueError("population aggregation_factor must be at least 1.")

        with rasterio.open(self.source(source_id)) as raster:
            source = raster.read(1, masked=True)
            rows = math.ceil(raster.height / factor)
            columns = math.ceil(raster.width / factor)
            shape = (rows * factor, columns * factor)
            values = np.zeros(shape, dtype="float64")
            valid = np.zeros(shape, dtype="int32")
            values[: raster.height, : raster.width] = np.clip(
                source.filled(0), 0, None
            )
            valid[: raster.height, : raster.width] = ~np.ma.getmaskarray(source)
            population = values.reshape(rows, factor, columns, factor).sum((1, 3))
            source_cells = valid.reshape(rows, factor, columns, factor).sum((1, 3))

            records = []
            for row, column in np.argwhere(source_cells > 0):
                row_start, column_start = int(row * factor), int(column * factor)
                height = min(factor, raster.height - row_start)
                width = min(factor, raster.width - column_start)
                window = Window(column_start, row_start, width, height)
                left, bottom, right, top = bounds(window, raster.transform)
                records.append({
                    "uid": f"{source_id}:{row_start}:{column_start}",
                    "class": "gridded_population",
                    "population": float(population[row, column]),
                    "geometry": box(left, bottom, right, top),
                    "geometry_method": "aggregated_source_grid",
                    "observed_at": self.options.get("observed_at", pd.NA),
                    "valid_from": self.options.get("valid_from", pd.NA),
                    "valid_to": self.options.get("valid_to", pd.NA),
                    "source_id": source_id,
                    "source_uid": (
                        f"{row_start}:{row_start + height}:"
                        f"{column_start}:{column_start + width}"
                    ),
                    "source_cell_count": int(source_cells[row, column]),
                })

            result = _finalize_frame(
                pd.DataFrame(records),
                schema_id="population",
                crs=str(raster.crs),
            )
        result["population"] = pd.to_numeric(
            result["population"], errors="coerce"
        ).astype("Float64")
        result["source_cell_count"] = pd.to_numeric(
            result["source_cell_count"], errors="coerce"
        ).astype("Int64")
        _write_geodataframe(result, self.output())
        return result
