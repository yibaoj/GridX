"""Technical, economic, environmental, and other parameter standardization."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from .base import _Standardizer
from .schema import REQUIRED_COLUMNS, _finalize_frame, _write_dataframe, time_bounds


PARAMETER_GROUPS = ("technical", "economic", "environmental", "others")
PARAMETER_OPTIONAL_COLUMNS = (
    "capacity_min_mw", "capacity_max_mw", "currency_year", "derivation",
    "is_derived", "location", "mapping_rule_id", "notes", "quality",
    "reference_url", "scenario", "selector_json", "source_name",
    "source_provider", "voltage_kv",
)
DEFAULT_QUALITY_ORDER = (
    "official_observation",
    "source",
    "standard_type_proxy",
    "derived",
    "generic",
    "interpolated",
    "proxy",
    "low",
    "model_default",
)

MATCHING_RULES = pd.DataFrame([
    {
        "rank": -1,
        "criterion": "ineligible",
        "direction": "excluded",
        "description": "No named candidate passes all applicability constraints.",
    },
    {
        "rank": 0,
        "criterion": "eligible",
        "direction": "accepted",
        "description": (
            "A single named candidate passes dataset, asset selectors, capacity, "
            "voltage, time, location, scenario, and selector_json constraints."
        ),
    },
    {
        "rank": 1,
        "criterion": "exact uid",
        "direction": "preferred",
        "description": "Prefer applies_to_uid equal to the target asset uid.",
    },
    {
        "rank": 2,
        "criterion": "specificity",
        "direction": "higher first",
        "description": "Prefer the candidate matching more explicit conditions.",
    },
    {
        "rank": 3,
        "criterion": "priority",
        "direction": "lower first",
        "description": "Apply the optional per-call source priority.",
    },
    {
        "rank": 4,
        "criterion": "quality",
        "direction": "configured order",
        "description": "Apply quality_order from standard_data.toml.",
    },
    {
        "rank": 5,
        "criterion": "observed time",
        "direction": "latest first",
        "description": "Prefer the most recently observed eligible record.",
    },
    {
        "rank": 6,
        "criterion": "equal values",
        "direction": "equivalent",
        "description": (
            "Equal-rank candidates have equivalent values and units."
        ),
    },
    {
        "rank": 7,
        "criterion": "ambiguous",
        "direction": "unresolved",
        "description": "Equal-rank candidates conflict in value or unit.",
    },
])


class ParameterData(pd.DataFrame):
    """Standard parameter table with reporting and resolution methods."""

    @property
    def _constructor(self) -> type["ParameterData"]:
        return ParameterData

    @property
    def matching_rules(self) -> pd.DataFrame:
        """Return the complete ordered matching policy."""

        rules = MATCHING_RULES.copy()
        quality_order = self.attrs.get("quality_order", DEFAULT_QUALITY_ORDER)
        rules["configured_value"] = pd.NA
        rules.loc[
            rules["criterion"].eq("quality"), "configured_value"
        ] = " > ".join(quality_order)
        return rules

    def count(self, by: str | Iterable[str] = "group") -> pd.DataFrame:
        """Count records and canonical names by selected categories."""

        keys = [by] if isinstance(by, str) else list(by)
        unknown = set(keys).difference(self.columns)
        if unknown:
            raise KeyError(f"Unknown parameter count fields: {sorted(unknown)}")
        frame = self if "is_derived" in self else self.assign(is_derived=False)
        result = (
            frame.groupby(keys, dropna=False)
            .agg(
                records=("uid", "size"),
                names=("name", "nunique"),
                sources=("source_id", "nunique"),
                datasets=("applies_to_dataset", "nunique"),
                classes=("class", "nunique"),
                derived_records=(
                    "is_derived",
                    lambda values: int(values.fillna(False).sum()),
                ),
            )
            .reset_index()
        )
        if keys == ["group"]:
            count_columns = [
                "records", "names", "sources", "datasets", "classes",
                "derived_records",
            ]
            result = (
                pd.DataFrame({"group": PARAMETER_GROUPS})
                .merge(result, on="group", how="left")
                .fillna({column: 0 for column in count_columns})
            )
            result[count_columns] = result[count_columns].astype("Int64")
        return result

    def validate(
        self,
        *,
        conflict_tolerance: float | None = None,
    ) -> "ParameterValidationReport":
        """Return structural, coverage, and conflict validation details."""

        tolerance = (
            float(self.attrs.get("conflict_tolerance", 1e-6))
            if conflict_tolerance is None else conflict_tolerance
        )
        return validate_parameter_data(
            self,
            conflict_tolerance=tolerance,
            quality_order=self.attrs.get("quality_order", DEFAULT_QUALITY_ORDER),
        )

    def resolve(
        self,
        assets: Mapping[str, object] | pd.Series | pd.DataFrame,
        names: str | Iterable[str],
        *,
        dataset_id: str,
        at: object = None,
        scenario: str | None = None,
        locations: Iterable[str] = (),
        selector_context: Mapping[str, object] | None = None,
        source_priority: Mapping[str, int] | None = None,
        quality_order: Iterable[str] | None = None,
        include_candidates: bool = False,
    ) -> pd.DataFrame | tuple[pd.DataFrame, pd.DataFrame]:
        """Resolve names for one or many assets, optionally with candidate details."""

        declared_dataset = _declared_dataset_id(assets)
        if declared_dataset is not None and declared_dataset != dataset_id:
            raise ValueError(
                f"asset dataset {declared_dataset!r} conflicts with "
                f"dataset_id={dataset_id!r}."
            )
        resolver = _ParameterResolver(
            self,
            source_priority=source_priority,
            name_aliases=self.attrs.get("name_aliases", {}),
            quality_order=(
                quality_order
                if quality_order is not None
                else self.attrs.get("quality_order", DEFAULT_QUALITY_ORDER)
            ),
            conflict_tolerance=float(
                self.attrs.get("conflict_tolerance", 1e-6)
            ),
        )
        locations = tuple(locations)
        if not isinstance(assets, pd.DataFrame):
            return resolver.resolve(
                _with_selector_context(assets, selector_context),
                names,
                dataset_id=dataset_id,
                at=at,
                scenario=scenario,
                locations=locations,
                include_candidates=include_candidates,
            )

        resolved_tables = []
        candidate_tables = []
        for _, asset in assets.iterrows():
            asset_locations = _asset_locations(asset, locations)
            result = resolver.resolve(
                _with_selector_context(asset, selector_context),
                names,
                dataset_id=dataset_id,
                at=at,
                scenario=scenario,
                locations=asset_locations,
                include_candidates=include_candidates,
            )
            if include_candidates:
                resolved, candidates = result
                resolved_tables.append(resolved)
                if not candidates.empty:
                    candidate_tables.append(candidates)
            else:
                resolved_tables.append(result)
        resolved = (
            pd.concat(resolved_tables, ignore_index=True)
            if resolved_tables else pd.DataFrame()
        )
        if not include_candidates:
            return resolved
        candidates = (
            pd.concat(candidate_tables, ignore_index=True)
            if candidate_tables else pd.DataFrame()
        )
        return resolved, candidates


def _with_selector_context(
    asset: Mapping[str, object] | pd.Series,
    context: Mapping[str, object] | None,
) -> dict[str, object]:
    """Add explicit selector fields without overriding asset information."""

    target = dict(asset)
    for key, value in (context or {}).items():
        existing = target.get(key)
        if not _missing(existing) and existing != value:
            raise ValueError(
                f"selector_context[{key!r}]={value!r} conflicts with "
                f"asset value {existing!r}."
            )
        target[key] = value
    return target


def _asset_locations(
    asset: Mapping[str, object] | pd.Series,
    fallback: Iterable[str],
) -> tuple[str, ...]:
    values = asset.get("location_hierarchy", fallback)
    if _missing(values):
        return ()
    if isinstance(values, str):
        return (values,)
    return tuple(str(value) for value in values)


def _declared_dataset_id(assets: object) -> str | None:
    attrs = getattr(assets, "attrs", {})
    for name in ("standard_dataset_id", "mapping_dataset_id"):
        value = attrs.get(name)
        if not _missing(value):
            return str(value)
    if isinstance(assets, Mapping):
        for name in ("standard_dataset_id", "mapping_dataset_id", "dataset_id"):
            value = assets.get(name)
            if not _missing(value):
                return str(value)
    return None


def as_parameter_data(data: pd.DataFrame) -> ParameterData:
    """Attach the parameter API while preserving dataframe metadata."""

    result = ParameterData(data)
    result.attrs = data.attrs.copy()
    return result


class _ParameterStandardizer(_Standardizer):
    _MAPPING_COLUMNS = {
        "rule_id", "priority", "source", "source_type_pattern",
        "technology_pattern", "fuel_pattern", "dataset", "class", "subclass",
    }

    def build(self) -> pd.DataFrame:
        adapters = {
            "long_table": self._long_table_rows,
            "wide_table": self._wide_table_rows,
        }
        specs = self._source_specs(adapters)
        rows = [
            row
            for spec in specs
            for row in adapters[str(spec["adapter"])](spec)
        ]

        frame = self._normalize_rows(pd.DataFrame(rows))
        frame = frame.reindex(columns=(
            *REQUIRED_COLUMNS["parameter"], *PARAMETER_OPTIONAL_COLUMNS,
        ))
        frame = frame.sort_values("uid", kind="stable").reset_index(drop=True)
        string_columns = (
            "source_name", "quality", "notes", "reference_url",
            "source_provider", "location", "currency_year", "selector_json",
            "scenario", "derivation",
        )
        result = _finalize_frame(
            frame,
            schema_id="parameter",
            string_columns=tuple(
                column for column in string_columns if column in frame
            ),
        )
        result = as_parameter_data(result)
        result.attrs["quality_order"] = tuple(
            self.options.get("quality_order", DEFAULT_QUALITY_ORDER)
        )
        result.attrs["name_aliases"] = dict(
            self.options.get("name_aliases", {})
        )
        result.attrs["conflict_tolerance"] = float(
            self.options.get("conflict_tolerance", 1e-6)
        )
        report = validate_parameter_data(
            result,
            conflict_tolerance=float(
                self.options.get("conflict_tolerance", 1e-6)
            ),
            quality_order=self.options.get(
                "quality_order", DEFAULT_QUALITY_ORDER
            ),
        )
        report.write(self.output("report"))
        if not report.ok:
            raise ValueError(
                "Parameter validation failed; inspect "
                f"{self.output('report')}."
            )
        _write_dataframe(result, self.output("data"))
        return result

    def _source_specs(
        self,
        adapters: Mapping[str, object],
    ) -> list[Mapping[str, object]]:
        specs = self.options.get("sources", ())
        if not specs:
            raise ValueError("parameter.options.sources must not be empty.")
        source_ids = []
        for spec in specs:
            if not isinstance(spec, Mapping):
                raise TypeError("Each parameter source must be a TOML table.")
            missing = {"source_id", "adapter"}.difference(spec)
            if missing:
                raise ValueError(
                    f"Parameter source is missing: {sorted(missing)}"
                )
            source_id = str(spec["source_id"])
            adapter = str(spec["adapter"])
            if adapter not in adapters:
                raise ValueError(
                    f"Unknown parameter adapter {adapter!r} for {source_id!r}."
                )
            if "quality" in spec and "quality_column" in spec:
                raise ValueError(
                    f"{source_id} must configure either a fixed quality or "
                    "quality_column, not both."
                )
            source_ids.append(source_id)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("Each parameter source_id must be configured once.")
        return list(specs)

    def _normalize_rows(self, frame: pd.DataFrame) -> pd.DataFrame:
        for column, default in {
            "source_name": pd.NA,
            "selector_json": "{}",
            "scenario": pd.NA,
            "is_derived": False,
            "derivation": pd.NA,
        }.items():
            if column not in frame:
                frame[column] = default
        frame["source_name"] = frame["source_name"].fillna(
            frame["name"]
        )
        aliases = self.options.get("name_aliases", {})
        frame["name"] = frame["name"].map(
            lambda value: _canonical_name(value, aliases)
        )
        frame["group"] = frame["name"].map(_parameter_group)
        network = frame["applies_to_dataset"].eq("network")
        frame.loc[
            network & frame["class"].isin(["line", "cable"]), "class"
        ] = "branch"
        frame.loc[
            network & frame["class"].eq("transformer"), ["class", "subclass"]
        ] = ["transformer", "ac_transformer"]
        converter = network & (
            frame["class"].eq("converter")
            | (
                frame["class"].eq("station")
                & frame["subclass"].eq("converter")
            )
        )
        frame.loc[converter, ["class", "subclass"]] = [
            "converter", "ac_dc_converter"
        ]
        if "fuel" not in frame:
            frame["fuel"] = frame.get("fuel_technology", pd.NA)
        elif "fuel_technology" in frame:
            frame["fuel"] = frame["fuel"].fillna(frame["fuel_technology"])
        frame["selector_json"] = frame.apply(_selector_json, axis=1)
        if "quality" in frame:
            frame["quality"] = frame["quality"].replace(
                self.options.get("quality_aliases", {})
            )
        quality = frame.get("quality", pd.Series(pd.NA, index=frame.index))
        derived = quality.astype("string").isin(
            {"derived", "interpolated", "proxy", "model_default"}
        )
        frame["is_derived"] = frame["is_derived"].fillna(False).astype(bool) | derived
        frame.loc[
            frame["is_derived"] & frame["derivation"].isna(), "derivation"
        ] = frame.get("notes")
        return frame

    def _long_table_rows(
        self,
        spec: Mapping[str, object],
    ) -> list[dict]:
        source_id = str(spec["source_id"])
        frame = pd.read_csv(self.source(source_id))
        name_column = str(spec.get("name_column", "parameter_name"))
        value_column = str(spec.get("value_column", "value"))
        unit_column = str(spec.get("unit_column", "unit"))
        required = {name_column, value_column, unit_column}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(
                f"{source_id} is missing long-table columns: {sorted(missing)}"
            )
        rule_sets = self._mapping_rule_sets(spec)
        rows = []
        for _, row in frame.iterrows():
            value = pd.to_numeric(row.get(value_column), errors="coerce")
            if pd.isna(value):
                continue
            source_uid = self._source_uid(row, spec)
            dataset, asset_class, subclass, rule_id = self._classify(
                row, spec, rule_sets
            )
            data = row.to_dict()
            data.pop(name_column, None)
            data.pop("parameter_group", None)
            data["source_name"] = row.get(name_column)
            data["name"] = row.get(name_column)
            data.update(self._required_fields(
                source_id,
                source_uid,
                dataset,
                asset_class,
                subclass,
                applies_to_uid=self._source_value(row, spec, "applies_to_uid"),
                status=self._source_value(row, spec, "status"),
                observed_at=self._source_value(row, spec, "observed_at"),
                valid_from=self._source_value(row, spec, "valid_from"),
                valid_to=self._source_value(row, spec, "valid_to"),
            ))
            data["value"] = float(value)
            data["unit"] = row.get(unit_column)
            data["mapping_rule_id"] = rule_id
            self._canonical_metadata(data, row, spec)
            rows.append(data)
        return rows

    def _wide_table_rows(
        self,
        spec: Mapping[str, object],
    ) -> list[dict]:
        source_id = str(spec["source_id"])
        frame = pd.read_csv(self.source(source_id))
        parameter_units = dict(spec.get("parameters", {}))
        if not parameter_units:
            raise ValueError(f"{source_id} wide_table requires parameters.")
        id_columns = list(spec.get("source_uid_columns", ()))
        required = {*id_columns, *parameter_units}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(
                f"{source_id} is missing wide-table columns: {sorted(missing)}"
            )
        rule_sets = self._mapping_rule_sets(spec)
        rows = []
        for _, row in frame.iterrows():
            for name, unit in parameter_units.items():
                value = pd.to_numeric(row.get(name), errors="coerce")
                if pd.isna(value):
                    continue
                dataset, asset_class, subclass, rule_id = self._classify(
                    row, spec, rule_sets
                )
                source_uid = self._source_uid(row, spec, suffix=name)
                data = {
                    **self._required_fields(
                        source_id, source_uid, dataset, asset_class, subclass,
                        observed_at=self._source_value(row, spec, "observed_at"),
                        valid_from=self._source_value(row, spec, "valid_from"),
                        valid_to=self._source_value(row, spec, "valid_to"),
                    ),
                    "name": name,
                    "source_name": name,
                    "value": float(value),
                    "unit": unit,
                    "mapping_rule_id": rule_id,
                }
                voltage_column = spec.get("voltage_column")
                if voltage_column and pd.notna(row.get(str(voltage_column))):
                    data["voltage_kv"] = [float(row[str(voltage_column)])]
                self._canonical_metadata(data, row, spec)
                rows.append(data)
        return rows

    def _mapping_rule_sets(
        self,
        spec: Mapping[str, object],
    ) -> list[pd.DataFrame]:
        source = str(spec.get("mapping_source", spec["source_id"]))
        files = [self.options.get("class_mapping_file"), spec.get("mapping_file")]
        return [
            self._read_mapping_rules(
                self.manager.project_root / str(path), source
            )
            for path in files
            if path
        ]

    def _read_mapping_rules(self, path: Path, source: str) -> pd.DataFrame:
        rules = pd.read_csv(path, keep_default_na=False)
        missing = self._MAPPING_COLUMNS.difference(rules.columns)
        if missing:
            raise ValueError(f"Parameter mapping is missing: {sorted(missing)}")
        selected = rules.loc[rules["source"].eq(source)].copy()
        if selected.empty:
            return selected
        selected["priority"] = pd.to_numeric(selected["priority"], errors="raise")
        if selected["rule_id"].eq("").any() or selected["rule_id"].duplicated().any():
            raise ValueError(f"Invalid parameter mapping rule IDs in {path}.")
        return selected.sort_values(["priority", "rule_id"])

    def _classify(
        self,
        row: pd.Series,
        spec: Mapping[str, object],
        rule_sets: list[pd.DataFrame],
    ) -> tuple[object, object, object, object]:
        dataset = self._source_value(row, spec, "applies_to_dataset")
        asset_class = self._source_value(row, spec, "class")
        subclass = self._source_value(row, spec, "subclass")
        rule_id = row.get("mapping_rule_id", pd.NA)
        if pd.notna(dataset) and pd.notna(asset_class):
            return dataset, asset_class, subclass, rule_id

        values = {
            "source_type_pattern": self._raw_mapping_value(
                row, spec, "source_type"
            ),
            "technology_pattern": self._raw_mapping_value(
                row, spec, "technology"
            ),
            "fuel_pattern": self._raw_mapping_value(row, spec, "fuel"),
        }
        for rules in rule_sets:
            for _, rule in rules.iterrows():
                if all(
                    re.search(rule[column] or ".*", values[column], re.I)
                    for column in values
                ):
                    return (
                        rule["dataset"], rule["class"],
                        rule["subclass"] or pd.NA, rule["rule_id"],
                    )
        return dataset, asset_class, subclass, rule_id

    @staticmethod
    def _source_value(
        row: pd.Series,
        spec: Mapping[str, object],
        name: str,
    ) -> object:
        if name in spec:
            return spec[name]
        column = str(spec.get(f"{name}_column", name))
        return row.get(column, pd.NA)

    @staticmethod
    def _raw_mapping_value(
        row: pd.Series,
        spec: Mapping[str, object],
        name: str,
    ) -> str:
        column = str(spec.get(f"{name}_column", name))
        value = row.get(column, "")
        return "" if pd.isna(value) else str(value)

    @staticmethod
    def _source_uid(
        row: pd.Series,
        spec: Mapping[str, object],
        suffix: str | None = None,
    ) -> str:
        column = spec.get("source_uid_column", "source_uid")
        if column in row.index and pd.notna(row.get(str(column))):
            parts = [str(row[str(column)])]
        else:
            columns = list(spec.get("source_uid_columns", ()))
            if not columns:
                raise ValueError(
                    f"{spec['source_id']} requires source_uid_column or "
                    "source_uid_columns."
                )
            parts = [str(row[column]) for column in columns]
        if spec.get("source_uid_suffix") is not None:
            parts.append(str(spec["source_uid_suffix"]))
        if suffix is not None:
            parts.append(suffix)
        return ":".join(parts)

    def _canonical_metadata(
        self,
        data: dict[str, object],
        row: pd.Series,
        spec: Mapping[str, object],
    ) -> None:
        for name in (
            "quality", "notes", "reference_url", "source_provider",
            "location", "scope", "standard_type", "scenario",
        ):
            value = self._source_value(row, spec, name)
            if pd.notna(value):
                data[name] = value
        if pd.isna(data.get("source_provider")):
            source_id = str(spec["source_id"])
            data["source_provider"] = self.manager.raw_data.catalog.loc[
                source_id, "provider"
            ]

    def _required_fields(
        self,
        source_id: str,
        source_uid: str,
        applies_to_dataset: object,
        asset_class: object,
        subclass: object,
        *,
        applies_to_uid: object = pd.NA,
        status: object = pd.NA,
        observed_at: object = pd.NA,
        valid_from: object = pd.NA,
        valid_to: object = pd.NA,
    ) -> dict:
        return {
            "uid": f"{source_id}:{source_uid}",
            "applies_to_dataset": applies_to_dataset,
            "applies_to_uid": applies_to_uid,
            "class": asset_class,
            "subclass": subclass,
            "status": status,
            "observed_at": observed_at,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "source_id": source_id,
            "source_uid": source_uid,
            "source_version": self.manager.raw_data.catalog.loc[
                source_id, "version"
            ],
        }

def _canonical_name(
    value: object,
    aliases: Mapping[str, str] | None = None,
) -> object:
    if pd.isna(value) or not str(value).strip():
        return pd.NA
    name = re.sub(
        r"_+", "_", re.sub(r"[^a-z0-9]+", "_", str(value).lower())
    ).strip("_")
    normalized_aliases = {
        str(key).strip().lower(): str(target).strip().lower()
        for key, target in (aliases or {}).items()
    }
    return normalized_aliases.get(name, name)


def _parameter_group(source_name: object) -> str:
    name = str(_canonical_name(source_name))
    if name in {"fuel", "commodity"} or any(token in name for token in (
        "co2", "carbondioxide", "carbon_intensity", "emission",
        "capture_rate", "c_in_fuel", "c_stored",
    )):
        return "environmental"
    if name in {"c_b", "c_v", "yield_biochar"} or any(token in name for token in (
        "investment", "fom", "vom", "fuel_price", "cost", "discount",
        "price", "value_of_lost_load", "surcharge", "economic_lifetime",
    )):
        return "economic"
    if any(token in name for token in (
        "committable", "minimum_output", "ramp_", "minimum_up_time",
        "minimum_down_time", "startup_time", "charging_efficiency",
        "discharging_efficiency", "standing_loss", "soc", "cyclic_",
        "efficiency", "lifetime", "capacity", "energy_to_power", "input",
        "output", "temperature", "fill_level", "motor_size", "p_nom_ratio",
        "r_ohm", "x_ohm", "c_nf", "max_i", "q_mm2", "alpha", "i0_",
        "pfe_", "sn_", "vn_", "vk_", "vkr_", "tap_", "shift_",
        "nominal_voltage", "s_nom",
    )):
        return "technical"
    return "others"


def _selector_json(row: pd.Series) -> str:
    raw = row.get("selector_json")
    if pd.isna(raw) or not str(raw).strip():
        selectors: dict[str, object] = {}
    elif isinstance(raw, Mapping):
        selectors = dict(raw)
    else:
        try:
            selectors = json.loads(str(raw))
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Invalid selector_json for {row.get('uid')}: {error}"
            ) from error
    if not isinstance(selectors, dict):
        raise ValueError(f"selector_json for {row.get('uid')} must be an object.")
    for column in ("fuel", "standard_type"):
        value = row.get(column)
        if pd.isna(value) or (column == "fuel" and value == row.get("class")):
            continue
        selectors[column] = value
    technology = row.get("technology")
    if (
        pd.notna(technology)
        and (
            pd.isna(row.get("applies_to_dataset"))
            or pd.isna(row.get("class"))
        )
    ):
        selectors["technology"] = technology
    for column, value in row.items():
        if not column.startswith("selector_") or column == "selector_json":
            continue
        if pd.isna(value):
            continue
        key = column.removeprefix("selector_")
        if isinstance(value, str) and value.strip().startswith(("[", "{")):
            value = json.loads(value)
        selectors[key] = value.item() if hasattr(value, "item") else value
    return json.dumps(selectors, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ParameterValidationReport:
    """Inspectable standard-parameter validation result."""

    summary: pd.DataFrame
    issues: pd.DataFrame
    coverage: pd.DataFrame
    conflicts: pd.DataFrame
    unit_variants: pd.DataFrame

    @property
    def ok(self) -> bool:
        return self.issues.empty or not self.issues["severity"].eq("error").any()

    def write(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ok": self.ok,
            "summary": _json_records(self.summary),
            "issues": _json_records(self.issues),
            "coverage": _json_records(self.coverage),
            "conflicts": _json_records(self.conflicts),
            "unit_variants": _json_records(self.unit_variants),
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )


def validate_parameter_data(
    data: pd.DataFrame,
    *,
    conflict_tolerance: float = 1e-6,
    quality_order: Iterable[str] = DEFAULT_QUALITY_ORDER,
) -> ParameterValidationReport:
    """Check record validity, source coverage, and exact-scope conflicts."""

    issue_rows: list[dict[str, object]] = []

    def issue(
        severity: str,
        code: str,
        message: str,
        uid: object = pd.NA,
    ) -> None:
        issue_rows.append({
            "severity": severity,
            "code": code,
            "uid": uid,
            "message": message,
        })

    for column in (
        "uid", "name", "group", "value", "unit",
        "source_id", "source_uid", "source_version",
    ):
        if column not in data:
            issue("error", "missing_column", f"Required column {column!r} is absent.")
            continue
        for uid in data.loc[data[column].isna(), "uid"].head(100):
            issue("error", "missing_value", f"{column} is missing.", uid)

    for uid in data.loc[data["uid"].duplicated(keep=False), "uid"].unique():
        issue("error", "duplicate_uid", "Parameter uid is not unique.", uid)

    noncanonical = data.loc[
        data["name"].ne(data["name"].map(_canonical_name)), ["uid", "name"]
    ]
    for row in noncanonical.itertuples(index=False):
        issue(
            "error", "noncanonical_name",
            f"Parameter name {row.name!r} is not canonical.", row.uid,
        )

    invalid_groups = data.loc[
        ~data["group"].isin(PARAMETER_GROUPS),
        ["uid", "group"],
    ]
    for row in invalid_groups.itertuples(index=False):
        issue(
            "error", "invalid_parameter_group",
            f"Unknown parameter group {row.group!r}.", row.uid,
        )

    known_qualities = set(quality_order)
    for quality in sorted(
        set(data["quality"].dropna().astype(str)).difference(known_qualities)
    ):
        issue(
            "warning", "unknown_quality",
            f"Quality {quality!r} is not present in quality_order.", quality,
        )

    expected_groups = data["name"].map(_parameter_group)
    for row in data.loc[
        data["group"].ne(expected_groups), ["uid", "name", "group"]
    ].itertuples(index=False):
        issue(
            "error", "inconsistent_group",
            f"{row.name!r} is classified as {row.group!r}.", row.uid,
        )

    for row in data[["uid", "selector_json"]].itertuples(index=False):
        try:
            selectors = json.loads(row.selector_json)
            if not isinstance(selectors, dict):
                raise TypeError("selector must be a JSON object")
        except (json.JSONDecodeError, TypeError) as error:
            issue("error", "invalid_selector", str(error), row.uid)

    bad_ranges = data.loc[
        data["capacity_min_mw"].notna()
        & data["capacity_max_mw"].notna()
        & data["capacity_min_mw"].gt(data["capacity_max_mw"]),
        "uid",
    ]
    for uid in bad_ranges:
        issue("error", "invalid_capacity_range", "capacity_min_mw exceeds maximum.", uid)

    per_unit = data["unit"].astype("string").isin({"p.u.", "per unit"})
    bounded = data["name"].str.contains(
        r"(?:_pu$|^minimum_soc|^maximum_soc|capture_rate)",
        regex=True,
        na=False,
    )
    for uid in data.loc[
        per_unit & bounded & ~data["value"].between(0, 1), "uid"
    ]:
        issue("error", "invalid_per_unit_value", "Per-unit value is outside [0, 1].", uid)

    derived_without_method = data.loc[
        data["is_derived"].fillna(False) & data["derivation"].isna(), "uid"
    ]
    for uid in derived_without_method:
        issue("warning", "missing_derivation", "Derived value lacks a derivation.", uid)

    unit_variants = (
        data.groupby("name", dropna=False)["unit"]
        .agg(lambda values: tuple(sorted(values.dropna().unique())))
    )
    unit_variants = unit_variants[unit_variants.map(len).gt(1)]
    for name, units in unit_variants.items():
        preview = ", ".join(units[:4])
        suffix = f", ... ({len(units)} total)" if len(units) > 4 else ""
        issue(
            "warning", "multiple_units",
            f"Canonical name has multiple units: {preview}{suffix}.", name,
        )
    unit_variant_table = pd.DataFrame({
        "name": unit_variants.index,
        "unit_count": unit_variants.map(len).values,
        "units": unit_variants.map("; ".join).values,
    }).reset_index(drop=True)

    conflicts = _parameter_conflicts(data, conflict_tolerance)
    for row in conflicts.itertuples(index=False):
        issue(
            "warning",
            "conflicting_candidates",
            f"{row.candidate_count} values share the same applicability scope.",
            row.name,
        )

    coverage_keys = [
        "applies_to_dataset", "class", "subclass", "group"
    ]
    coverage = (
        data.groupby(coverage_keys, dropna=False)
        .agg(
            records=("uid", "size"),
            parameters=("name", "nunique"),
            sources=("source_id", "nunique"),
        )
        .reset_index()
        .sort_values(coverage_keys, na_position="last")
        .reset_index(drop=True)
    )
    issues = pd.DataFrame(
        issue_rows,
        columns=("severity", "code", "uid", "message"),
    )
    summary_values = {
        "records": len(data),
        "sources": data["source_id"].nunique(),
        "names": data["name"].nunique(),
        "unmapped_records": int(data["applies_to_dataset"].isna().sum()),
        "derived_records": int(data["is_derived"].fillna(False).sum()),
        "conflict_groups": len(conflicts),
        "multiple_unit_names": len(unit_variants),
        "errors": int(issues["severity"].eq("error").sum()),
        "warnings": int(issues["severity"].eq("warning").sum()),
    }
    descriptions = {
        "records": "Standard parameter records.",
        "sources": "Distinct raw source IDs.",
        "names": "Distinct canonical parameter names.",
        "unmapped_records": "Records not yet mapped to a standard dataset.",
        "derived_records": "Calculated, interpolated, proxy, or default values.",
        "conflict_groups": "Exact applicability scopes containing different values.",
        "multiple_unit_names": "Canonical names represented by more than one unit.",
        "errors": "Validation failures that block a standard build.",
        "warnings": "Auditable issues that do not block a standard build.",
    }
    summary = pd.DataFrame({
        "key": list(summary_values),
        "value": summary_values.values(),
        "description": [descriptions[key] for key in summary_values],
    }).reset_index(drop=True)
    return ParameterValidationReport(
        summary, issues, coverage, conflicts, unit_variant_table
    )


def _parameter_conflicts(
    data: pd.DataFrame,
    tolerance: float,
) -> pd.DataFrame:
    scope_columns = [
        "name", "applies_to_dataset", "applies_to_uid", "class",
        "subclass", "status", "capacity_min_mw", "capacity_max_mw",
        "location", "scenario", "valid_from", "valid_to", "selector_json",
        "voltage_scope_kv",
    ]
    frame = data.copy()
    frame["voltage_scope_kv"] = (
        frame["voltage_kv"].map(
            lambda values: pd.NA if _missing(values) else json.dumps(list(values))
        )
        if "voltage_kv" in frame else pd.NA
    )
    for column in scope_columns:
        if column not in frame:
            frame[column] = pd.NA
    rows = []
    for key, group in frame.groupby(scope_columns, dropna=False, sort=False):
        units = group["unit"].dropna().unique()
        values = group["value"].dropna().astype(float).to_numpy()
        same_value = (
            len(units) == 1
            and len(values)
            and np.allclose(values, values[0], rtol=tolerance, atol=tolerance)
        )
        if len(group) < 2 or same_value:
            continue
        row = dict(zip(scope_columns, key, strict=True))
        row.update({
            "candidate_count": len(group),
            "values": "; ".join(
                f"{value:g} {unit}"
                for value, unit in zip(group["value"], group["unit"], strict=True)
            ),
            "source_ids": "; ".join(sorted(group["source_id"].unique())),
            "parameter_uids": "; ".join(sorted(group["uid"])),
        })
        rows.append(row)
    return pd.DataFrame(
        rows,
        columns=(*scope_columns, "candidate_count", "values", "source_ids", "parameter_uids"),
    )


class _ParameterResolver:
    """Resolve parameter candidates with deterministic, inspectable rules."""

    def __init__(
        self,
        parameters: pd.DataFrame,
        *,
        source_priority: Mapping[str, int] | None = None,
        name_aliases: Mapping[str, str] | None = None,
        quality_order: Iterable[str] = DEFAULT_QUALITY_ORDER,
        conflict_tolerance: float = 1e-6,
    ) -> None:
        self.parameters = parameters
        self.source_priority = dict(source_priority or {})
        self.name_aliases = dict(name_aliases or {})
        self.quality_rank = {
            quality: rank for rank, quality in enumerate(quality_order)
        }
        self.conflict_tolerance = conflict_tolerance

    def _candidate_details(
        self,
        target: Mapping[str, object] | pd.Series,
        name: str,
        *,
        dataset_id: str,
        at: object = None,
        scenario: str | None = None,
        locations: Iterable[str] = (),
    ) -> pd.DataFrame:
        """Evaluate every candidate and record its acceptance or rejection reason."""

        target = dict(target)
        name = _canonical_name(name, self.name_aliases)
        candidates = self.parameters.loc[
            self.parameters["name"].eq(name)
        ].copy()
        evaluations = [
            self._evaluate(
                row, target, dataset_id, at, scenario, set(locations)
            )
            for _, row in candidates.iterrows()
        ]
        if candidates.empty:
            return pd.DataFrame(columns=(
                "uid", "eligible", "rejection_reason", "exact_uid",
                "specificity", "effective_priority", "quality_rank",
                "matched_conditions",
            ))
        candidates[[
            "eligible", "rejection_reason", "exact_uid", "specificity",
            "effective_priority", "quality_rank", "observed_rank",
            "matched_conditions",
        ]] = pd.DataFrame(evaluations, index=candidates.index)
        return candidates.sort_values(
            ["eligible", "exact_uid", "specificity", "effective_priority",
             "quality_rank", "observed_rank", "uid"],
            ascending=[False, False, False, True, True, False, True],
        ).reset_index(drop=True)

    def resolve(
        self,
        target: Mapping[str, object] | pd.Series,
        names: str | Iterable[str],
        *,
        dataset_id: str,
        at: object = None,
        scenario: str | None = None,
        locations: Iterable[str] = (),
        include_candidates: bool = False,
    ) -> pd.DataFrame | tuple[pd.DataFrame, pd.DataFrame]:
        """Return resolved values and, optionally, all candidate decisions."""

        names = [names] if isinstance(names, str) else list(names)
        rows = []
        candidate_tables = []
        target_uid = dict(target).get("uid", pd.NA)

        for name in names:
            requested_name = str(name)
            candidates = self._candidate_details(
                target,
                name,
                dataset_id=dataset_id,
                at=at,
                scenario=scenario,
                locations=locations,
            )
            eligible = candidates.loc[candidates.get("eligible", False)].copy()
            base = {
                "target_uid": target_uid,
                "name": _canonical_name(name, self.name_aliases),
                "candidate_count": len(candidates),
                "eligible_count": len(eligible),
                "selected_parameter_uid": pd.NA,
                "value": pd.NA,
                "unit": pd.NA,
                "match_rank": -1,
                "match_result": "ineligible",
                "match_info": _eligibility_info(candidates),
                "group": pd.NA,
                "source_id": pd.NA,
                "source_uid": pd.NA,
                "source_version": pd.NA,
                "reference_url": pd.NA,
                "quality": pd.NA,
                "observed_at": pd.NA,
            }
            if eligible.empty:
                rows.append(base)
                if include_candidates and not candidates.empty:
                    candidate_tables.append(_candidate_output(
                        candidates, target_uid, requested_name, -1
                    ))
                continue
            top, match_rank, match_result, match_info = _select_by_rank(
                eligible, target_uid
            )
            same = _equivalent_values(top, self.conflict_tolerance)
            base.update({
                "match_rank": match_rank,
                "match_result": match_result,
                "match_info": match_info,
            })
            if len(top) > 1 and not same:
                base.update({
                    "match_rank": 7,
                    "match_result": "ambiguous",
                    "match_info": _candidate_uid_info(top),
                })
                rows.append(base)
                if include_candidates:
                    candidate_tables.append(_candidate_output(
                        candidates, target_uid, requested_name, 7,
                        top=top, ambiguous=True,
                    ))
                continue
            selected = top.iloc[0]
            base.update({
                "selected_parameter_uid": selected["uid"],
                "value": selected["value"],
                "unit": selected["unit"],
                **{
                    column: selected.get(column)
                    for column in (
                        "group", "source_id", "source_uid", "source_version",
                        "reference_url", "quality", "observed_at",
                    )
                },
            })
            rows.append(base)
            if include_candidates:
                candidate_tables.append(_candidate_output(
                    candidates, target_uid, requested_name, match_rank,
                    top=top, selected_uid=selected["uid"],
                    equivalent=len(top) > 1,
                ))
        resolved = pd.DataFrame(rows)
        if not include_candidates:
            return resolved
        candidate_details = (
            pd.concat(candidate_tables, ignore_index=True)
            if candidate_tables else pd.DataFrame()
        )
        return resolved, candidate_details

    def _evaluate(
        self,
        row: pd.Series,
        target: dict[str, object],
        dataset_id: str,
        at: object,
        scenario: str | None,
        locations: set[str],
    ) -> tuple[bool, str, bool, int, int, int, int, str]:
        reasons = []
        matched = []
        specificity = 0
        exact_uid = False

        def require(column: str, target_key: str | None = None) -> None:
            nonlocal specificity, exact_uid
            expected = row.get(column)
            if pd.isna(expected):
                return
            key = target_key or column
            actual = target.get(key)
            if _missing(actual):
                reasons.append(f"missing_target:{key}")
            elif str(actual) != str(expected):
                reasons.append(f"mismatch:{key}")
            else:
                specificity += 1
                matched.append(key)
                exact_uid = exact_uid or column == "applies_to_uid"

        if pd.notna(row.get("applies_to_dataset")):
            if str(row["applies_to_dataset"]) != dataset_id:
                reasons.append("mismatch:dataset_id")
            else:
                specificity += 1
                matched.append("dataset_id")
        require("applies_to_uid", "uid")
        for column in ("class", "subclass", "status"):
            require(column)

        capacity = target.get("capacity_mw", target.get("power_capacity_mw"))
        minimum, maximum = row.get("capacity_min_mw"), row.get("capacity_max_mw")
        if pd.notna(minimum) or pd.notna(maximum):
            if pd.isna(capacity):
                reasons.append("missing_target:capacity_mw")
            elif (pd.notna(minimum) and float(capacity) < float(minimum)) or (
                pd.notna(maximum) and float(capacity) >= float(maximum)
            ):
                reasons.append("outside_range:capacity_mw")
            else:
                specificity += 1
                matched.append("capacity_mw")

        if "voltage_kv" in row.index and row.get("voltage_kv") is not None:
            expected_voltage = row.get("voltage_kv")
            if not _missing(expected_voltage):
                actual_voltage = target.get("voltage_kv")
                expected_set = set(_as_values(expected_voltage))
                actual_set = (
                    set(_as_values(actual_voltage))
                    if not _missing(actual_voltage) else set()
                )
                if not expected_set.intersection(actual_set):
                    reasons.append("mismatch:voltage_kv")
                else:
                    specificity += 1
                    matched.append("voltage_kv")

        row_scenario = row.get("scenario")
        if pd.notna(row_scenario):
            if scenario is None:
                reasons.append("missing_context:scenario")
            elif row_scenario != scenario:
                reasons.append("mismatch:scenario")
            else:
                specificity += 1
                matched.append("scenario")

        row_location = row.get("location")
        if pd.notna(row_location):
            if not locations:
                reasons.append("missing_context:locations")
            elif str(row_location) not in locations:
                reasons.append("mismatch:location")
            else:
                specificity += 1
                matched.append("location")

        if pd.notna(row.get("valid_from")) or pd.notna(row.get("valid_to")):
            if at is None:
                reasons.append("missing_context:at")
            elif not _active_at(row, at):
                reasons.append("outside_valid_time")
            else:
                specificity += 1
                matched.append("valid_time")

        selector_text = row.get("selector_json")
        selectors = json.loads("{}" if pd.isna(selector_text) else selector_text)
        for key, condition in selectors.items():
            actual = target.get(key)
            if not _selector_matches(actual, condition):
                reasons.append(f"selector_mismatch:{key}")
            else:
                specificity += 1
                matched.append(f"selector:{key}")

        priority = self.source_priority.get(str(row.get("source_id")), 0)
        quality_rank = self.quality_rank.get(
            str(row.get("quality")), len(self.quality_rank)
        )
        return (
            not reasons,
            "; ".join(reasons),
            exact_uid,
            specificity,
            priority,
            quality_rank,
            _time_rank(row.get("observed_at")),
            "; ".join(matched),
        )


def _select_by_rank(
    eligible: pd.DataFrame,
    target_uid: object,
) -> tuple[pd.DataFrame, int, str, str]:
    """Narrow eligible candidates and identify the decisive matching rule."""

    remaining = eligible.copy()
    if len(remaining) == 1:
        candidate = remaining.iloc[0]
        if bool(candidate["exact_uid"]):
            return remaining, 1, "exact uid", f"asset_uid={target_uid}"
        return remaining, 0, "eligible", _candidate_uid_info(remaining)

    criteria = (
        (1, "exact uid", "exact_uid", "max"),
        (2, "specificity", "specificity", "max"),
        (3, "priority", "effective_priority", "min"),
        (4, "quality", "quality_rank", "min"),
        (5, "observed time", "observed_rank", "max"),
    )
    for rank, criterion, column, direction in criteria:
        best = (
            remaining[column].max()
            if direction == "max" else remaining[column].min()
        )
        narrowed = remaining.loc[remaining[column].eq(best)]
        if len(narrowed) == len(remaining):
            continue
        remaining = narrowed
        if len(remaining) == 1:
            candidate = remaining.iloc[0]
            return remaining, rank, criterion, _criterion_info(
                criterion, candidate, target_uid
            )
    return remaining, 6, "equal values", _candidate_uid_info(remaining)


def _criterion_info(
    criterion: str,
    candidate: pd.Series,
    target_uid: object,
) -> str:
    if criterion == "exact uid":
        return f"asset_uid={target_uid}"
    if criterion == "specificity":
        return (
            f"specificity={int(candidate['specificity'])}; "
            f"matched={candidate['matched_conditions']}"
        )
    if criterion == "priority":
        return (
            f"source_id={candidate['source_id']}; "
            f"source_priority={int(candidate['effective_priority'])}"
        )
    if criterion == "quality":
        return (
            f"quality={candidate.get('quality')}; "
            f"quality_rank={int(candidate['quality_rank'])}"
        )
    return f"observed_at={candidate.get('observed_at')}"


def _candidate_output(
    candidates: pd.DataFrame,
    target_uid: object,
    requested_name: str,
    outcome_rank: int,
    *,
    top: pd.DataFrame | None = None,
    selected_uid: object = pd.NA,
    equivalent: bool = False,
    ambiguous: bool = False,
) -> pd.DataFrame:
    """Format candidate-level decisions against every matching rank."""

    details = candidates.copy()
    top = details.iloc[0:0] if top is None else top
    top_uids = set(top["uid"].astype(str))
    winner = top.iloc[0] if not top.empty else None
    details["selection_status"] = np.where(
        details["eligible"], "not_selected", "ineligible"
    )
    details["match_rank"] = -1
    if winner is not None:
        eligible = details["eligible"]
        details.loc[eligible, "match_rank"] = details.loc[eligible].apply(
            lambda row: _elimination_rank(row, winner, outcome_rank), axis=1
        )
    selected = details["uid"].eq(selected_uid) if pd.notna(selected_uid) else False
    top_mask = details["uid"].astype(str).isin(top_uids)
    if ambiguous:
        details.loc[top_mask, "selection_status"] = "ambiguous"
    elif pd.notna(selected_uid):
        details.loc[selected, "selection_status"] = "selected"
        if equivalent:
            details.loc[top_mask & ~selected, "selection_status"] = "equivalent"

    details["rank_m1_ineligible"] = details["rejection_reason"].mask(
        details["eligible"]
    )
    details["rank_0_eligible"] = details["eligible"]
    details["rank_1_exact_uid"] = details["exact_uid"].where(
        details["match_rank"].ge(1)
    )
    details["rank_2_specificity"] = details.apply(
        lambda row: (
            f"{int(row['specificity'])}: {row['matched_conditions']}"
            if row["match_rank"] >= 2 else pd.NA
        ),
        axis=1,
    )
    details["rank_3_priority"] = details["effective_priority"].where(
        details["match_rank"].ge(3)
    )
    details["rank_4_quality"] = details.apply(
        lambda row: (
            f"{row.get('quality')} ({int(row['quality_rank'])})"
            if row["match_rank"] >= 4 else pd.NA
        ),
        axis=1,
    )
    details["rank_5_observed_time"] = details["observed_at"].where(
        details["match_rank"].ge(5)
    )
    details["rank_6_equal_values"] = pd.Series(
        pd.NA, index=details.index, dtype="boolean"
    )
    details.loc[details["match_rank"].ge(6), "rank_6_equal_values"] = (
        top_mask & equivalent
    )
    details["rank_7_ambiguous"] = pd.Series(
        pd.NA, index=details.index, dtype="boolean"
    )
    details.loc[details["match_rank"].ge(7), "rank_7_ambiguous"] = (
        top_mask & ambiguous
    )
    details["target_uid"] = target_uid
    details["requested_name"] = requested_name
    rank_columns = [
        "selection_status", "match_rank", "rank_m1_ineligible",
        "rank_0_eligible", "rank_1_exact_uid", "rank_2_specificity",
        "rank_3_priority", "rank_4_quality", "rank_5_observed_time",
        "rank_6_equal_values", "rank_7_ambiguous", "target_uid",
        "requested_name",
    ]
    internal = {
        "eligible", "rejection_reason", "exact_uid", "specificity",
        "effective_priority", "quality_rank", "observed_rank",
        "matched_conditions",
    }
    remaining = [
        column for column in details.columns
        if column not in {*rank_columns, *internal}
    ]
    return details.loc[:, [*rank_columns, *remaining]]


def _elimination_rank(
    candidate: pd.Series,
    winner: pd.Series,
    outcome_rank: int,
) -> int:
    for rank, column in (
        (1, "exact_uid"), (2, "specificity"), (3, "effective_priority"),
        (4, "quality_rank"), (5, "observed_rank"),
    ):
        if candidate[column] != winner[column]:
            return rank
    return outcome_rank


def _eligibility_info(candidates: pd.DataFrame) -> str:
    if candidates.empty:
        return "no parameter has the requested name"
    reasons = candidates["rejection_reason"].dropna().astype(str)
    counts: dict[str, int] = {}
    for reason in reasons:
        for item in filter(None, reason.split("; ")):
            counts[item] = counts.get(item, 0) + 1
    return "; ".join(
        f"{reason} ({count})" for reason, count in sorted(counts.items())
    ) or "no eligible candidate"


def _candidate_uid_info(candidates: pd.DataFrame) -> str:
    return "parameter_uids=" + ";".join(
        sorted(candidates["uid"].dropna().astype(str).unique())
    )


def _selector_matches(actual: object, condition: object) -> bool:
    if _missing(actual):
        return False
    if isinstance(condition, dict):
        if "eq" in condition and actual != condition["eq"]:
            return False
        if "in" in condition and actual not in condition["in"]:
            return False
        if "not_in" in condition and actual in condition["not_in"]:
            return False
        if "min" in condition and float(actual) < float(condition["min"]):
            return False
        if "max" in condition and float(actual) >= float(condition["max"]):
            return False
        return True
    if isinstance(condition, list):
        return actual in condition
    return actual == condition


def _active_at(row: pd.Series, value: object) -> bool:
    timestamp = pd.Timestamp(value)
    timestamp = timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
    if pd.notna(row.get("valid_from")) and timestamp < time_bounds(row["valid_from"])[0]:
        return False
    if pd.notna(row.get("valid_to")) and timestamp >= time_bounds(row["valid_to"])[1]:
        return False
    return True


def _time_rank(value: object) -> int:
    if pd.isna(value):
        return -1
    try:
        return time_bounds(value)[0].value
    except (TypeError, ValueError):
        return -1


def _equivalent_values(frame: pd.DataFrame, tolerance: float) -> bool:
    if frame["unit"].nunique(dropna=False) != 1:
        return False
    values = frame["value"].astype(float).to_numpy()
    return bool(np.allclose(values, values[0], rtol=tolerance, atol=tolerance))


def _missing(value: object) -> bool:
    if isinstance(value, (list, tuple, set, np.ndarray)):
        return len(value) == 0
    return bool(pd.isna(value))


def _as_values(value: object) -> list[object]:
    if isinstance(value, (list, tuple, set, np.ndarray)):
        return list(value)
    return [value]


def _json_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    return [
        {
            key: None if _missing(value) else value.item()
            if hasattr(value, "item") else value
            for key, value in row.items()
        }
        for row in frame.to_dict("records")
    ]
