"""Administrative spatial-unit standardization."""

from __future__ import annotations

import json
import warnings

import geopandas as gpd
import pandas as pd
from shapely import make_valid
from shapely.geometry import shape

from .base import _Standardizer
from .schema import _finalize_frame, _write_geodataframe


class _SpatialStandardizer(_Standardizer):
    def build(self) -> gpd.GeoDataFrame:
        source_id = self.config["source_ids"][0]
        content = json.loads(self.source(source_id).read_text())
        records = []
        for feature in content["features"]:
            properties = feature.get("properties") or {}
            record_id = str(properties.get("adcode") or properties.get("code") or "")
            geometry = shape(feature["geometry"])
            if not geometry.is_valid:
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore",
                        message="invalid value encountered in make_valid",
                        category=RuntimeWarning,
                    )
                    geometry = make_valid(geometry)
            record = {
                key: (
                    json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (dict, list))
                    else value
                )
                for key, value in properties.items()
                if key not in {"uid", "geometry"}
            }
            record.update({
                "uid": f"datav:{record_id}",
                "level": "province",
                "geometry": geometry,
                "geometry_method": "source_geometry_repaired",
                "observed_at": pd.NA,
                "valid_from": pd.NA,
                "valid_to": pd.NA,
                "source_id": source_id,
                "source_uid": record_id,
            })
            records.append(record)
        frame = pd.DataFrame(records)
        result = _finalize_frame(
            frame,
            schema_id="spatial",
            string_columns=tuple(
                column
                for column in frame.columns
                if column not in {"geometry"}
                and column not in {
                    "uid", "level", "geometry_method", "observed_at",
                    "valid_from", "valid_to", "source_id", "source_uid",
                }
            ),
        )
        _write_geodataframe(result, self.output())
        return result
