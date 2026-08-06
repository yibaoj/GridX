"""Shared PyPSA parameter contract for validation and conversion."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping
import tomllib

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ParameterSpec:
    """One canonical case parameter mapped to a PyPSA attribute."""

    key: str
    component: str
    target: str
    name: str
    field: str | None
    unit: str
    unit_factors: Mapping[str, float]
    formula: str
    fallback: float | bool
    required: bool
    selector: Mapping[str, object]


class PyPSAParameterManifest:
    """Read, validate, and apply the declarative PyPSA parameter contract."""

    def __init__(self, path: Path, config: Mapping[str, object]) -> None:
        self.path = path
        self.version = int(config["version"])
        self.pypsa_version = str(config["pypsa_version"])
        self.specs = tuple(
            ParameterSpec(
                key=str(row["key"]),
                component=str(row["component"]),
                target=str(row["target"]),
                name=str(row["name"]),
                field=str(row["field"]) if row.get("field") else None,
                unit=str(row["unit"]),
                unit_factors={
                    str(unit): float(factor)
                    for unit, factor in row["unit_factors"].items()
                },
                formula=str(row["formula"]),
                fallback=row["fallback"],
                required=bool(row["required"]),
                selector=dict(row.get("selector", {})),
            )
            for row in config["parameter"]
        )
        keys = [spec.key for spec in self.specs]
        if len(keys) != len(set(keys)):
            raise ValueError("PyPSA parameter manifest keys must be unique.")
        self._by_key = {spec.key: spec for spec in self.specs}

    def table(self) -> pd.DataFrame:
        """Return the inspectable parameter contract."""

        return pd.DataFrame([{
            "key": spec.key,
            "component": spec.component,
            "target": spec.target,
            "name": spec.name,
            "field": spec.field,
            "unit": spec.unit,
            "accepted_units": tuple(spec.unit_factors),
            "formula": spec.formula,
            "fallback": spec.fallback,
            "required": spec.required,
            "selector": dict(spec.selector),
        } for spec in self.specs])

    def spec(self, key: str) -> ParameterSpec:
        """Return one manifest entry by stable key."""

        try:
            return self._by_key[key]
        except KeyError as error:
            raise KeyError(f"Unknown PyPSA parameter key {key!r}.") from error

    def values(
        self,
        key: str,
        assets: pd.DataFrame,
        parameters: pd.DataFrame,
    ) -> np.ndarray:
        """Return canonical-unit values indexed like assets, applying fallback."""

        spec = self.spec(key)
        if spec.component not in {"branch", "transformer", "converter", "generator", "storage"}:
            raise ValueError(f"Unsupported manifest component {spec.component!r}.")
        converted = _converted_parameters(parameters, spec)
        selected = converted.drop_duplicates("uid").set_index("uid")["_value"]
        values = pd.to_numeric(
            assets["uid"].astype(str).map(selected), errors="coerce"
        ).to_numpy(float)
        if spec.field and spec.field in assets:
            field_values = pd.to_numeric(
                assets[spec.field], errors="coerce"
            ).to_numpy(float)
            values = np.where(np.isfinite(field_values), field_values, values)
        return np.where(np.isfinite(values), values, float(spec.fallback))

    def validate(
        self,
        parameters: Mapping[str, pd.DataFrame],
        assets: Mapping[str, pd.DataFrame],
    ) -> pd.DataFrame:
        """Validate coverage and units for every declared backend parameter."""

        rows = []
        for spec in self.specs:
            targets = _select_assets(assets[spec.component], spec.selector)
            target_uids = set(targets["uid"].astype(str))
            source = parameters.get(spec.component, pd.DataFrame())
            if source.empty:
                named = pd.DataFrame(columns=("uid", "name", "value", "unit"))
            else:
                named = source.loc[
                    source["name"].eq(spec.name)
                    & source["uid"].astype(str).isin(target_uids)
                    & source["value"].notna()
                ].copy()
            converted = _converted_parameters(named, spec)
            valid_uids = set(converted["uid"].astype(str))
            if spec.field and spec.field in targets:
                field_values = pd.to_numeric(targets[spec.field], errors="coerce")
                valid_uids.update(
                    targets.loc[field_values.notna(), "uid"].astype(str)
                )
            unit_mismatches = named.loc[
                ~named["unit"].astype(str).isin(spec.unit_factors)
            ]
            conflicts = _conflicting_uids(converted)
            valid_uids.difference_update(conflicts)
            target_count = len(target_uids)
            resolved = len(valid_uids)
            missing = target_count - resolved
            status = (
                "not_applicable" if target_count == 0
                else "pass" if missing == 0
                else "fallback"
            )
            rows.append({
                "check": "backend_parameter_manifest",
                "component": spec.component,
                "name": spec.key,
                "target": spec.target,
                "status": status,
                "value": resolved / target_count if target_count else np.nan,
                "required": spec.required,
                "detail": (
                    f"{resolved}/{target_count} resolved as {spec.unit}; "
                    f"fallback={spec.fallback!r}; "
                    f"unit_mismatches={len(unit_mismatches)}; "
                    f"conflicting_assets={len(conflicts)}"
                ),
            })
        return pd.DataFrame(rows)


@lru_cache(maxsize=8)
def load_pypsa_manifest(path: str | Path) -> PyPSAParameterManifest:
    """Load and cache one PyPSA parameter manifest."""

    resolved = Path(path).expanduser()
    if not resolved.is_absolute() and not resolved.exists():
        resolved = Path(__file__).resolve().parents[3] / resolved
    resolved = resolved.resolve()
    with resolved.open("rb") as file:
        return PyPSAParameterManifest(resolved, tomllib.load(file))


def _converted_parameters(
    parameters: pd.DataFrame,
    spec: ParameterSpec,
) -> pd.DataFrame:
    if parameters.empty:
        return pd.DataFrame(columns=("uid", "_value"))
    selected = parameters.loc[
        parameters["name"].eq(spec.name) & parameters["value"].notna()
    ].copy()
    selected["_factor"] = selected["unit"].astype(str).map(spec.unit_factors)
    selected = selected.loc[selected["_factor"].notna()]
    selected["_value"] = (
        pd.to_numeric(selected["value"], errors="coerce")
        * selected["_factor"]
    )
    return selected.loc[selected["_value"].notna(), ["uid", "_value"]]


def _select_assets(
    assets: pd.DataFrame,
    selector: Mapping[str, object],
) -> pd.DataFrame:
    selected = pd.Series(True, index=assets.index)
    for column, expected in selector.items():
        if column not in assets:
            selected &= False
            continue
        values = expected if isinstance(expected, list) else [expected]
        selected &= assets[column].isin(values)
    return assets.loc[selected]


def _conflicting_uids(parameters: pd.DataFrame) -> set[str]:
    if parameters.empty:
        return set()
    counts = parameters.groupby("uid")["_value"].nunique(dropna=True)
    return set(counts.loc[counts.gt(1)].index.astype(str))
