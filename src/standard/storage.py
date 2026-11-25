"""Storage asset standardization."""

from __future__ import annotations

import json
import re

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from .asset_mapping import _asset_in_scope, _GemMixin
from .base import _Standardizer
from .time import valid_time
from .schema import (
    _finalize_frame,
    _numeric,
    _points,
    _snake_case,
    _write_geodataframe,
)


def _doe_time(value):
    """DOE exports US-format dates; accept ISO dates in supplemental exports."""

    if value is None or pd.isna(value) or not str(value).strip():
        return pd.NA
    for format_string in ("%m-%d-%Y", "%Y-%m-%d", "%Y"):
        parsed = pd.to_datetime(str(value).strip(), format=format_string, errors="coerce")
        if pd.notna(parsed):
            return str(value).strip() if format_string == "%Y" else parsed.date().isoformat()
    return pd.NA


class _StorageStandardizer(_Standardizer, _GemMixin):
    def build(self) -> gpd.GeoDataFrame:
        rows = []
        for source_id in self.config["source_ids"]:
            metadata = self.manager.raw_data.catalog.loc[source_id]
            reader = json.loads(metadata.get("options_json") or "{}").get("reader")
            if reader == "curated_storage":
                rows.extend(self._curated_storage(source_id))
            elif self.source(source_id).suffix.lower() == ".json":
                rows.extend(self._doe_storage(source_id))
            else:
                rows.extend(self._gem_storage(source_id))
        result = _finalize_frame(
            pd.DataFrame(rows),
            schema_id="storage",
            string_columns=(
                "name",
                "source_province",
                "source_city",
                "technology",
            ),
        )
        for column in ("power_capacity_mw", "energy_capacity_mwh", "duration_h"):
            result[column] = pd.to_numeric(
                result[column], errors="coerce"
            ).astype("Float64")
        _write_geodataframe(result, self.output())
        return result

    def _curated_storage(self, source_id: str) -> list[dict[str, object]]:
        frame = pd.read_csv(self.source(source_id), dtype={"source_uid": str})
        rules = self._mapping_rules(
            self.manager.project_root / self.options["class_mapping_file"],
            "curated_storage",
        )
        if frame["source_uid"].isna().any() or frame["source_uid"].duplicated().any():
            raise ValueError(f"{source_id} requires unique, nonempty source_uid values.")
        rows = []
        for row in frame.to_dict("records"):
            status = _snake_case(row["status"])
            allowed = self.options.get("include_statuses", [])
            if allowed and status not in {_snake_case(value) for value in allowed}:
                continue
            matched = rules[rules["technology_pattern"].map(
                lambda pattern: bool(re.search(pattern or ".*", row["technology"], re.I))
            )]
            if matched.empty or matched.iloc[0]["dataset"] != "storage":
                raise ValueError(f"{source_id}: unclassified technology {row['technology']!r}.")
            rule = matched.iloc[0]
            rows.append({
                **row,
                "uid": f"{source_id}:{row['source_uid']}",
                "class": rule["class"],
                "subclass": rule["subclass"],
                "status": status,
                "geometry": _points([row["longitude"]], [row["latitude"]])[0],
                "valid_from": valid_time(row["valid_from"]),
                "valid_to": valid_time(row["valid_to"]),
                "source_id": source_id,
                "mapping_rule_id": rule["rule_id"],
            })
        return rows

    def _gem_storage(self, source_id: str) -> list[dict[str, object]]:
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
        frame = frame.loc[classification["dataset"].eq("storage")].copy()
        classification = classification.loc[frame.index]
        longitude = pd.to_numeric(frame["Longitude"], errors="coerce")
        latitude = pd.to_numeric(frame["Latitude"], errors="coerce")
        geometries = _points(longitude, latitude)
        return [
            {
                "uid": f"gem:{row['GEM unit/phase ID']}",
                "class": classification.loc[index, "class"],
                "subclass": classification.loc[index, "subclass"],
                "status": _snake_case(row["Status"]),
                "power_capacity_mw": _numeric(row["Capacity (MW)"]),
                "energy_capacity_mwh": pd.NA,
                "duration_h": pd.NA,
                "voltage_kv": None,
                "geometry": geometries[position],
                "geometry_method": (
                    "source_coordinates"
                    if geometries[position] is not None
                    else pd.NA
                ),
                "observed_at": self.source_observed_at(source_id),
                "valid_from": valid_time(row.get("Start year")),
                "valid_to": valid_time(row.get("Retired year")),
                "source_id": source_id,
                "source_uid": row["GEM unit/phase ID"],
                "mapping_rule_id": classification.loc[index, "mapping_rule_id"],
                "name": row["Plant / Project name"],
                "source_province": row.get("Subnational unit (state, province)"),
                "source_city": row.get("City"),
                "longitude": longitude.loc[index],
                "latitude": latitude.loc[index],
                "technology": row["Technology"],
            }
            for position, (index, row) in enumerate(frame.iterrows())
        ]

    def _doe_storage(self, source_id: str) -> list[dict[str, object]]:
        projects = json.loads(self.source(source_id).read_text())
        rules = self._mapping_rules(
            self.manager.project_root / self.options["class_mapping_file"],
            "doe",
        )
        rows = []
        for project in projects:
            if not _asset_in_scope(
                project.get("Country"), project.get("Status"),
                country_areas=self.options["country_areas"],
                include_statuses=self.options.get("include_statuses", []),
            ):
                continue
            technologies = sorted({
                subsystem.get("Storage Device", {}).get("Technology Mid-Type")
                for subsystem in (project.get("Subsystems") or [])
                if subsystem.get("Storage Device", {}).get("Technology Mid-Type")
            })
            raw_technology = "; ".join(technologies)
            matched = rules[rules["source"].eq("doe")]
            matched = matched[
                matched["technology_pattern"].map(
                    lambda pattern: bool(
                        re.search(pattern or ".*", raw_technology, re.I)
                    )
                )
            ]
            rule = matched.iloc[0] if not matched.empty else None
            storage_class = rule["class"] if rule is not None else "other_storage"
            subclass = (
                rule["subclass"] if rule is not None and rule["subclass"] else pd.NA
            )
            # GEM is the project-level pumped-hydro source to avoid double counting.
            if storage_class == "pumped_storage":
                continue
            longitude = _numeric(project.get("Longitude"))
            latitude = _numeric(project.get("Latitude"))
            geometry = (
                Point(longitude, latitude)
                if pd.notna(longitude) and pd.notna(latitude)
                else None
            )
            power = _numeric(project.get("Rated Power (kW)"))
            energy = _numeric(project.get("Storage Capacity (kWh)"))
            power_mw = power / 1000 if pd.notna(power) else pd.NA
            energy_mwh = energy / 1000 if pd.notna(energy) else pd.NA
            rows.append({
                "uid": f"doe:{project.get('ID')}",
                "class": storage_class,
                "subclass": subclass,
                "status": _snake_case(project.get("Status")),
                "power_capacity_mw": power_mw,
                "energy_capacity_mwh": energy_mwh,
                "duration_h": (
                    energy_mwh / power_mw
                    if pd.notna(energy_mwh) and pd.notna(power_mw) and power_mw > 0
                    else pd.NA
                ),
                "voltage_kv": None,
                "geometry": geometry,
                "geometry_method": "source_coordinates" if geometry else pd.NA,
                "observed_at": self.source_observed_at(source_id),
                "valid_from": _doe_time(project.get("Commissioned Date")),
                "valid_to": _doe_time(project.get("Decommissioned Date")),
                "source_id": source_id,
                "source_uid": str(project.get("ID")),
                "mapping_rule_id": rule["rule_id"] if rule is not None else pd.NA,
                "name": project.get("Project/Plant Name"),
                "source_province": project.get("State/Province"),
                "source_city": project.get("City"),
                "longitude": longitude,
                "latitude": latitude,
                "technology": raw_technology or pd.NA,
            })
        return rows
