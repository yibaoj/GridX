"""Administrative spatial-unit standardization."""

from __future__ import annotations

import json

import geopandas as gpd
import pandas as pd
from shapely import make_valid
from shapely.geometry import shape

from .base import _Standardizer
from .schema import _finalize_entities, _write_geodataframe


class _SpatialStandardizer(_Standardizer):
    def build(self) -> gpd.GeoDataFrame:
        source_id = self.config["source_ids"][0]
        content = json.loads(self.source(source_id).read_text())
        records = []
        for feature in content["features"]:
            properties = feature.get("properties") or {}
            record_id = str(properties.get("adcode") or properties.get("code") or "")
            geometry = make_valid(shape(feature["geometry"]))
            records.append({
                "uid": f"datav:province:{record_id}",
                "type": "province",
                "technology": pd.NA,
                "status": pd.NA,
                "voltage_kv": None,
                "valid_from": pd.NA,
                "valid_to": pd.NA,
                "observed_at": pd.NA,
                "source_id": source_id,
                "source_record_id": record_id,
                "geometry_method": "source_geometry",
                "name": properties.get("name"),
                "parent_uid": pd.NA,
                "geometry": geometry,
            })
        result = _finalize_entities(
            pd.DataFrame(records),
            extra_columns=("name", "parent_uid"),
            string_columns=("name", "parent_uid"),
        )
        _write_geodataframe(result, self.output())
        return result
