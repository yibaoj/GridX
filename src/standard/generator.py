"""Generator asset standardization."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd

from .asset_mapping import _asset_in_scope, _GemMixin
from .base import _Standardizer
from .schema import (
    _finalize_frame,
    _nullable_boolean,
    _partial_time,
    _points,
    _snake_case,
    _write_geodataframe,
)


class _GeneratorStandardizer(_Standardizer, _GemMixin):
    def build(self) -> gpd.GeoDataFrame:
        source_id = self.config["source_ids"][0]
        frame = self._gem_records(source_id, self.options["sheet"])
        frame = frame.loc[[
            _asset_in_scope(
                row["Country/area"], row["Status"],
                country_areas=self.options["country_areas"],
                include_statuses=self.options.get("include_statuses", []),
            )
            for _, row in frame.iterrows()
        ]].copy()
        classification = self._classify_gem(
            frame,
            self.manager.project_root / self.options["class_mapping_file"],
        )
        frame = frame.loc[classification["dataset"].eq("generator")].copy()
        classification = classification.loc[frame.index]
        capacity = pd.to_numeric(frame["Capacity (MW)"], errors="coerce")
        longitude = pd.to_numeric(frame["Longitude"], errors="coerce")
        latitude = pd.to_numeric(frame["Latitude"], errors="coerce")
        result = pd.DataFrame({
            "uid": "gem:" + frame["GEM unit/phase ID"].astype("string"),
            "class": classification["class"],
            "subclass": classification["subclass"],
            "status": frame["Status"].map(_snake_case),
            "capacity_mw": capacity,
            "fuel": frame["Fuel (combustion only)"].map(self._fuel),
            "chp": _nullable_boolean(frame.get("CHP", pd.Series(pd.NA, index=frame.index))),
            "ccs": (
                _nullable_boolean(frame.get("CCS", pd.Series(pd.NA, index=frame.index)))
                | frame["Technology"].fillna("").str.contains("CCS", case=False)
            ),
            "voltage_kv": [None] * len(frame),
            "geometry": _points(longitude, latitude),
            "geometry_method": np.where(
                longitude.notna() & latitude.notna(), "source_coordinates", pd.NA
            ),
            "observed_at": self.source_observed_at(source_id),
            "valid_from": frame.get("Start year", pd.Series(pd.NA, index=frame.index)).map(
                _partial_time
            ),
            "valid_to": frame.get("Retired year", pd.Series(pd.NA, index=frame.index)).map(
                _partial_time
            ),
            "source_id": source_id,
            "source_uid": frame["GEM unit/phase ID"],
            "mapping_rule_id": classification["mapping_rule_id"],
            "project_uid": "gem:" + frame["GEM location ID"].astype("string"),
            "name": frame["Plant / Project name"],
            "unit_name": frame.get("Unit / Phase name"),
            "fuel_raw": frame["Fuel (combustion only)"],
            "owner": frame.get("Owner(s)"),
            "operator": frame.get("Operator(s)"),
            "location_accuracy": frame.get("Location accuracy"),
            "source_province": frame.get("Subnational unit (state, province)"),
            "source_city": frame.get("City"),
            "longitude": longitude,
            "latitude": latitude,
            "technology": frame["Technology"],
        })
        result = _finalize_frame(
            result,
            schema_id="generator",
            string_columns=(
                "project_uid",
                "name",
                "unit_name",
                "fuel",
                "fuel_raw",
                "owner",
                "operator",
                "location_accuracy",
                "source_province",
                "source_city",
                "technology",
            ),
        )
        result["capacity_mw"] = pd.to_numeric(
            result["capacity_mw"], errors="coerce"
        ).astype("Float64")
        _write_geodataframe(result, self.output())
        return result
