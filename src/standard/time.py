"""Shared validity rules for partial ISO dates and source lifecycle tags."""

from __future__ import annotations

import pandas as pd

from .schema import _partial_time, time_bounds


def valid_time(value: object) -> object:
    """Keep a parseable partial ISO date; source placeholders mean unknown."""

    value = _partial_time(value)
    if pd.isna(value):
        return pd.NA
    try:
        time_bounds(value)
    except (ValueError, TypeError, OverflowError):
        return pd.NA
    return value


def network_validity(record: object) -> tuple[object, object]:
    """Prefer actual start, expected opening, construction; end before closing."""

    def first(names):
        for name in names:
            value = record.get(name) if hasattr(record, "get") else getattr(record, name, None)
            value = valid_time(value)
            if pd.isna(value):
                continue
            return value
        return pd.NA

    return first(("valid_from", "start_date", "opening_date", "construction_date")), first(
        ("valid_to", "end_date", "closing_date")
    )


def active_at(record: object, at: object) -> bool:
    """Match overlapping date precision; empty boundaries impose no limit."""

    if at is None or str(at).strip() == "":
        return True
    lower, upper = valid_time(record.get("valid_from")), valid_time(record.get("valid_to"))
    if pd.isna(lower) and pd.isna(upper):
        return True
    start, end = time_bounds(at)
    return (
        (pd.isna(lower) or str(lower).strip() == "" or time_bounds(lower)[0] < end)
        and (pd.isna(upper) or str(upper).strip() == "" or time_bounds(upper)[1] > start)
    )


def validity_mask(frame: pd.DataFrame, at: object) -> pd.Series:
    """Select valid records without changing the canonical source table."""

    return pd.Series(
        [active_at(row, at) for _, row in frame.iterrows()], index=frame.index, dtype=bool
    )


def lifecycle_mask(frame: pd.DataFrame, at: object, statuses: list[str], *, missing=True) -> pd.Series:
    """Dates override source status; undated records fall back to status."""

    status = frame["status"].astype("string").str.strip().str.lower()
    allowed = {str(value).lower() for value in statuses}
    fallback = status.isin(allowed) | ((status.isna() | status.eq("")) & missing)
    if at is None or str(at).strip() == "":
        return fallback.fillna(False)
    dated = pd.Series(False, index=frame.index)
    for name in ("valid_from", "valid_to"):
        if name in frame:
            dated |= frame[name].map(valid_time).notna()
    return validity_mask(frame, at) & (dated | fallback).fillna(False)
