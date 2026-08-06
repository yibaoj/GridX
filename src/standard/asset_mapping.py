"""Ordered source-to-standard asset classification rules."""

from __future__ import annotations

from pathlib import Path
import re

import pandas as pd


def _normalize_scope_label(value: object) -> str:
    return re.sub(
        r"_+", "_", re.sub(r"[^a-z0-9]+", "_", str(value).lower())
    ).strip("_")


def _asset_in_scope(
    country: object,
    status: object,
    *,
    country_areas: list[str],
    include_statuses: list[str],
) -> bool:
    """Apply source-independent country and canonical-status filters."""

    country_name = _normalize_scope_label(country)
    country_name = "china" if country_name == "china_mainland" else country_name
    allowed_countries = {
        "china"
        if _normalize_scope_label(value) == "china_mainland"
        else _normalize_scope_label(value)
        for value in country_areas
    }
    allowed_statuses = {
        _normalize_scope_label(value) for value in include_statuses
    }
    return country_name in allowed_countries and (
        not allowed_statuses or _normalize_scope_label(status) in allowed_statuses
    )


class _GemMixin:
    _MAPPING_COLUMNS = {
        "rule_id",
        "priority",
        "source",
        "source_type_pattern",
        "technology_pattern",
        "fuel_pattern",
        "dataset",
        "class",
        "subclass",
    }

    @classmethod
    def _mapping_rules(cls, mapping_file: Path, source: str) -> pd.DataFrame:
        rules = pd.read_csv(mapping_file, keep_default_na=False)
        missing = cls._MAPPING_COLUMNS.difference(rules.columns)
        if missing:
            raise ValueError(f"Asset mapping is missing columns: {sorted(missing)}")
        if rules["rule_id"].eq("").any() or rules["rule_id"].duplicated().any():
            raise ValueError("Asset mapping rule_id values must be present and unique.")
        rules["priority"] = pd.to_numeric(rules["priority"], errors="raise")
        if rules.duplicated(["source", "priority"]).any():
            raise ValueError(
                "Asset mapping priorities must be unique within each source."
            )
        if not rules["dataset"].isin(
            {"network", "generator", "storage"}
        ).all():
            raise ValueError(
                "Asset mapping dataset must be network, generator, or storage."
            )
        if rules["class"].eq("").any():
            raise ValueError("Asset mapping class values must be present.")
        for column in (
            "source_type_pattern",
            "technology_pattern",
            "fuel_pattern",
        ):
            for rule_id, pattern in rules[["rule_id", column]].itertuples(index=False):
                try:
                    re.compile(pattern or ".*", re.I)
                except re.error as error:
                    raise ValueError(
                        f"Invalid {column} in mapping rule {rule_id}: {error}"
                    ) from error
        selected = rules.loc[rules["source"].eq(source)].copy()
        if selected.empty:
            raise ValueError(f"Asset mapping has no rules for source {source!r}.")
        return selected.sort_values(["priority", "rule_id"])

    def _gem_records(self, source_id: str, sheet: str) -> pd.DataFrame:
        frame = pd.read_excel(self.source(source_id), sheet_name=sheet, engine="openpyxl")
        required = {
            "Type",
            "Country/area",
            "Plant / Project name",
            "Capacity (MW)",
            "Status",
            "Technology",
            "Latitude",
            "Longitude",
            "GEM location ID",
            "GEM unit/phase ID",
            "Fuel (combustion only)",
        }
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"GEM workbook is missing columns: {sorted(missing)}")
        return frame

    @staticmethod
    def _classify_gem(frame: pd.DataFrame, mapping_file: Path) -> pd.DataFrame:
        rules = _GemMixin._mapping_rules(mapping_file, "gem")
        result = pd.DataFrame(
            {
                "dataset": pd.Series(pd.NA, index=frame.index, dtype="string"),
                "class": pd.Series(pd.NA, index=frame.index, dtype="string"),
                "subclass": pd.Series(pd.NA, index=frame.index, dtype="string"),
                "mapping_rule_id": pd.Series(
                    pd.NA, index=frame.index, dtype="string"
                ),
            }
        )
        source_type = frame["Type"].fillna("").astype(str)
        technology = frame["Technology"].fillna("").astype(str)
        fuel = frame["Fuel (combustion only)"].fillna("").astype(str)
        for _, rule in rules.iterrows():
            unmatched = result["dataset"].isna()
            mask = (
                unmatched
                & source_type.str.contains(
                    rule["source_type_pattern"] or ".*", case=False, regex=True
                )
                & technology.str.contains(
                    rule["technology_pattern"] or ".*", case=False, regex=True
                )
                & fuel.str.contains(
                    rule["fuel_pattern"] or ".*", case=False, regex=True
                )
            )
            result.loc[mask, ["dataset", "class"]] = [
                rule["dataset"], rule["class"]
            ]
            result.loc[mask, "mapping_rule_id"] = rule["rule_id"]
            if rule["subclass"]:
                result.loc[mask, "subclass"] = rule["subclass"]
        return result

    @staticmethod
    def _fuel(value: object) -> object:
        text = "" if pd.isna(value) else str(value).lower()
        patterns = (
            (r"\blng\b", "lng"),
            (r"natural gas", "natural_gas"),
            (r"blast furnace gas", "blast_furnace_gas"),
            (r"coke oven gas", "coke_oven_gas"),
            (r"coalbed methane|coal mine methane", "coal_methane"),
            (r"fuel oil|diesel|fossil liquids", "oil"),
            (r"hydrogen", "hydrogen_blend"),
        )
        matches = [label for pattern, label in patterns if re.search(pattern, text)]
        return ";".join(matches) if matches else (pd.NA if not text else text)
