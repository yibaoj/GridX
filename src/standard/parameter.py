"""Technical and economic parameter standardization."""

from __future__ import annotations

import pandas as pd

from .base import _Standardizer
from .schema import _numeric, _voltage_series


class _ParameterStandardizer(_Standardizer):
    _PARAMETERS = {
        "committable": ("boolean", "technical"),
        "minimum_output_pu": ("p.u.", "technical"),
        "ramp_up_pu_per_hour": ("p.u./h", "technical"),
        "ramp_down_pu_per_hour": ("p.u./h", "technical"),
        "minimum_up_time_h": ("h", "technical"),
        "minimum_down_time_h": ("h", "technical"),
        "startup_time_h": ("h", "technical"),
        "efficiency_override": ("p.u.", "technical"),
        "startup_cost_eur_per_mw": ("EUR/MW", "economic"),
        "shutdown_cost_eur_per_mw": ("EUR/MW", "economic"),
    }

    def build(self) -> pd.DataFrame:
        rows = []
        assumptions = pd.read_csv(
            self.manager.project_root / self.options["assumptions_file"]
        )
        required_columns = {
            "assumption_id",
            "applies_to_dataset",
            "type",
            "technology",
        }
        missing = required_columns.difference(assumptions.columns)
        if missing:
            raise ValueError(
                "Technical-economic assumptions are missing columns: "
                + ", ".join(sorted(missing))
            )
        if assumptions["assumption_id"].isna().any() or assumptions[
            "assumption_id"
        ].duplicated().any():
            raise ValueError("assumption_id values must be present and unique.")

        for _, row in assumptions.iterrows():
            for parameter_name, (unit, parameter_group) in self._PARAMETERS.items():
                if parameter_name not in assumptions or pd.isna(row.get(parameter_name)):
                    continue
                rows.append({
                    "uid": (
                        f"technical_economic_assumptions:{row['assumption_id']}:"
                        f"{parameter_name}"
                    ),
                    "applies_to_dataset": row["applies_to_dataset"],
                    "applies_to_uid": pd.NA,
                    "type": row["type"],
                    "technology": row["technology"],
                    "status": pd.NA,
                    "voltage_kv": None,
                    "valid_from": pd.NA,
                    "valid_to": pd.NA,
                    "observed_at": pd.NA,
                    "source_id": "technical_economic_assumptions",
                    "source_record_id": row["assumption_id"],
                    "parameter_name": parameter_name,
                    "value": float(row[parameter_name]),
                    "unit": unit,
                    "capacity_min_mw": _numeric(row.get("capacity_min_mw")),
                    "capacity_max_mw": _numeric(row.get("capacity_max_mw")),
                    "quality": row.get(f"{parameter_group}_quality"),
                    "notes": row.get(f"{parameter_group}_notes"),
                    "reference_url": row.get(f"{parameter_group}_source"),
                    "pypsa_technology": row.get("pypsa_technology"),
                    "fuel_technology": row.get("fuel_technology"),
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
                "reference_url": pd.NA,
                "pypsa_technology": row.get("technology"),
                "fuel_technology": pd.NA,
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
            "reference_url",
            "pypsa_technology",
            "fuel_technology",
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
