"""Technical and economic parameter standardization."""

from __future__ import annotations

import pandas as pd

from .base import _Standardizer
from .schema import _numeric, _voltage_series


class _ParameterStandardizer(_Standardizer):
    _UNITS = {
        "committable": "boolean",
        "minimum_output_pu": "p.u.",
        "ramp_up_pu_per_hour": "p.u./h",
        "ramp_down_pu_per_hour": "p.u./h",
        "minimum_up_time_h": "h",
        "minimum_down_time_h": "h",
        "startup_time_h": "h",
        "efficiency_override": "p.u.",
        "startup_cost_eur_per_mw": "EUR/MW",
        "shutdown_cost_eur_per_mw": "EUR/MW",
    }

    def build(self) -> pd.DataFrame:
        rows = []
        for source_name, filename in (
            ("config_generation_technical", self.options["technical_file"]),
            ("config_generation_economic", self.options["economic_file"]),
        ):
            table = pd.read_csv(self.manager.project_root / filename)
            for row_number, row in table.iterrows():
                target_type = self._target_type(row["generation_type"])
                technology = self._target_technology(
                    row["generation_type"], row.get("technology_pattern")
                )
                for parameter_name, unit in self._UNITS.items():
                    if parameter_name not in table or pd.isna(row.get(parameter_name)):
                        continue
                    rows.append({
                        "uid": f"{source_name}:row:{row_number}:{parameter_name}",
                        "applies_to_dataset": (
                            "storage"
                            if row["generation_type"] == "pumped_storage"
                            else "generation"
                        ),
                        "applies_to_uid": pd.NA,
                        "type": target_type,
                        "technology": technology,
                        "status": pd.NA,
                        "voltage_kv": None,
                        "valid_from": pd.NA,
                        "valid_to": pd.NA,
                        "observed_at": pd.NA,
                        "source_id": source_name,
                        "source_record_id": f"row:{row_number}",
                        "parameter_name": parameter_name,
                        "value": float(row[parameter_name]),
                        "unit": unit,
                        "capacity_min_mw": _numeric(row.get("capacity_min_mw")),
                        "capacity_max_mw": _numeric(row.get("capacity_max_mw")),
                        "quality": row.get("assumption_quality"),
                        "notes": row.get("notes"),
                    })

        pypsa_source = self.config["source_ids"][0]
        costs = pd.read_csv(self.source(pypsa_source))
        for index, row in costs.iterrows():
            value = pd.to_numeric(row.get("value"), errors="coerce")
            if pd.isna(value):
                continue
            rows.append({
                "uid": (
                    f"pypsa:{row.get('technology')}:{row.get('parameter')}:"
                    f"{self.options['pypsa_year']}"
                ),
                "applies_to_dataset": pd.NA,
                "applies_to_uid": pd.NA,
                "type": pd.NA,
                "technology": row.get("technology"),
                "status": pd.NA,
                "voltage_kv": None,
                "valid_from": str(self.options["pypsa_year"]),
                "valid_to": pd.NA,
                "observed_at": pd.NA,
                "source_id": pypsa_source,
                "source_record_id": str(index),
                "parameter_name": row.get("parameter"),
                "value": float(value),
                "unit": row.get("unit"),
                "capacity_min_mw": pd.NA,
                "capacity_max_mw": pd.NA,
                "quality": "source",
                "notes": row.get("description"),
            })
        result = pd.DataFrame(rows)
        for column in (
            "uid",
            "applies_to_dataset",
            "applies_to_uid",
            "type",
            "technology",
            "status",
            "valid_from",
            "valid_to",
            "observed_at",
            "source_id",
            "source_record_id",
            "parameter_name",
            "unit",
            "quality",
            "notes",
        ):
            result[column] = result[column].astype("string")
        result["voltage_kv"] = _voltage_series(result["voltage_kv"], result.index)
        for column in ("value", "capacity_min_mw", "capacity_max_mw"):
            result[column] = pd.to_numeric(result[column], errors="coerce").astype(
                "Float64"
            )
        if result["uid"].isna().any() or result["uid"].duplicated().any():
            raise ValueError("Parameter uid values must be present and unique.")
        path = self.output()
        path.parent.mkdir(parents=True, exist_ok=True)
        result.to_parquet(path, index=False)
        return result

    @staticmethod
    def _target_type(old_type: str) -> str:
        return {
            "natural_gas": "gas",
            "other_oil_gas": "other",
            "reservoir_hydropower": "hydropower",
            "run_of_river_hydropower": "hydropower",
            "other_hydropower": "hydropower",
            "utility_scale_solar": "solar",
            "solar_thermal": "solar",
            "onshore_wind": "wind",
            "offshore_wind": "wind",
        }.get(old_type, old_type)

    @staticmethod
    def _target_technology(old_type: str, pattern: object) -> object:
        if pd.isna(pattern) or pattern == ".*":
            return pd.NA
        if old_type == "natural_gas":
            return {
                "combined cycle": "combined_cycle",
                "gas turbine": "gas_turbine",
            }.get(str(pattern), pd.NA)
        return {
            "reservoir_hydropower": "reservoir",
            "run_of_river_hydropower": "run_of_river",
            "utility_scale_solar": "utility_scale_pv",
            "solar_thermal": "solar_thermal",
            "onshore_wind": "onshore",
            "offshore_wind": "offshore_fixed",
        }.get(old_type, pd.NA)
