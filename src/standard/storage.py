"""Storage asset standardization."""

from __future__ import annotations

import json
import re

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from .asset_mapping import _GemMixin
from .base import _Standardizer
from .schema import (
    _finalize_frame,
    _numeric,
    _partial_time,
    _points,
    _snake_case,
    _write_geodataframe,
)


class _StorageStandardizer(_Standardizer, _GemMixin):
    def build(self) -> gpd.GeoDataFrame:
        gem_source, doe_source = self.config["source_ids"]
        rows = self._gem_storage(gem_source)
        rows.extend(self._doe_storage(doe_source))
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

    def _gem_storage(self, source_id: str) -> list[dict[str, object]]:
        frame = self._gem_records(source_id, self.options["gem_sheet"])
        frame = frame[frame["Country/area"].isin(self.options["country_areas"])].copy()
        statuses = set(self.options.get("gem_statuses", []))
        if statuses:
            frame = frame[frame["Status"].isin(statuses)].copy()
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
                "observed_at": self.options.get("gem_observed_at"),
                "valid_from": _partial_time(row.get("Start year")),
                "valid_to": _partial_time(row.get("Retired year")),
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
        statuses = set(self.options.get("doe_statuses", []))
        rows = []
        for project in projects:
            if str(project.get("Country", "")).strip() != self.options["doe_country"]:
                continue
            if statuses and project.get("Status") not in statuses:
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
                "observed_at": self.options.get("doe_observed_at"),
                "valid_from": pd.NA,
                "valid_to": pd.NA,
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
