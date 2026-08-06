"""Resolve standard parameters for case components."""

from __future__ import annotations

from collections import defaultdict
import json

import numpy as np
import pandas as pd

from ..standard import ParameterData


PARAMETER_COLUMNS = (
    "uid", "component", "name", "group", "value", "unit",
    "match_rank", "match_result", "match_info", "selected_parameter_uid",
    "source_id", "source_uid", "source_version", "reference_url", "quality",
    "observed_at",
)


def resolve_parameters(
    assets: pd.DataFrame,
    parameters: ParameterData,
    *,
    dataset_id: str,
    component: str,
    at: object,
) -> pd.DataFrame:
    """Resolve all applicable names, caching assets with equal selectors."""

    scoped = parameters.loc[
        parameters["applies_to_dataset"].eq(dataset_id)
        & (
            parameters["class"].isna()
            | parameters["class"].isin(assets["class"].dropna().unique())
        )
    ]
    names = sorted(scoped["name"].dropna().unique())
    if assets.empty or not names:
        return pd.DataFrame(columns=PARAMETER_COLUMNS)

    selector_keys = _selector_keys(scoped)
    location_values = set(scoped["location"].dropna().astype(str))
    thresholds = sorted(set(
        pd.to_numeric(
            scoped[["capacity_min_mw", "capacity_max_mw"]].stack(),
            errors="coerce",
        ).dropna()
    ))
    grouped: dict[tuple, list[int]] = defaultdict(list)
    for index, asset in assets.reset_index(drop=True).iterrows():
        grouped[
            _signature(asset, selector_keys, thresholds, location_values)
        ].append(index)

    source = assets.reset_index(drop=True)
    exact_uids = set(parameters["applies_to_uid"].dropna().astype(str))
    rows = []
    for indices in grouped.values():
        if any(str(source.iloc[index]["uid"]) in exact_uids for index in indices):
            batches = [[index] for index in indices]
        else:
            batches = [indices]
        for batch in batches:
            target = source.iloc[batch[0]]
            resolved = parameters.resolve(
                target,
                names,
                dataset_id=dataset_id,
                at=at,
                locations=_locations(target),
            )
            for index in batch:
                copied = resolved.copy()
                copied["target_uid"] = source.iloc[index]["uid"]
                rows.append(copied)

    result = pd.concat(rows, ignore_index=True)
    result = result.rename(columns={"target_uid": "uid"})
    result.insert(1, "component", component)
    return result.reindex(columns=PARAMETER_COLUMNS)


def _selector_keys(parameters: pd.DataFrame) -> list[str]:
    keys = set()
    for value in parameters["selector_json"].dropna():
        try:
            keys.update(json.loads(value))
        except (TypeError, json.JSONDecodeError):
            continue
    return sorted(keys)


def _signature(
    asset: pd.Series,
    selector_keys: list[str],
    thresholds: list[float],
    location_values: set[str],
) -> tuple:
    capacity = asset.get("capacity_mw", asset.get("power_capacity_mw"))
    capacity = float(capacity) if pd.notna(capacity) else np.nan
    band = int(np.searchsorted(thresholds, capacity, side="right")) if pd.notna(capacity) else -1
    voltage = asset.get("voltage_kv")
    if isinstance(voltage, (list, tuple, np.ndarray)):
        voltage = tuple(voltage)
    values = [asset.get(name) for name in ("class", "subclass", "status")]
    matched_locations = tuple(
        sorted(set(_locations(asset)).intersection(location_values))
    )
    values.extend([voltage, band, matched_locations])
    values.extend(asset.get(name) for name in selector_keys)
    return tuple("<NA>" if _missing(value) else str(value) for value in values)


def _locations(asset: pd.Series) -> tuple[str, ...]:
    values = []
    for name in (
        "location", "source_city", "source_province", "admin_uid",
        "bus_admin_uid",
    ):
        value = asset.get(name)
        if not _missing(value):
            values.append(str(value))
    return tuple(dict.fromkeys(values))


def _missing(value: object) -> bool:
    if isinstance(value, (list, tuple, set, np.ndarray)):
        return len(value) == 0
    return bool(pd.isna(value))
