"""Shared schemas and serialization helpers for standard datasets."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
import re

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow as pa
from shapely.geometry import Point


DATASET_IDS = (
    "spatial",
    "network",
    "generation",
    "storage",
    "parameter",
    "load",
    "population",
    "resource",
)

__all__ = ["DATASET_IDS", "NetworkData", "time_bounds"]

ENTITY_COLUMNS = (
    "uid",
    "type",
    "technology",
    "status",
    "voltage_kv",
    "valid_from",
    "valid_to",
    "observed_at",
    "source_id",
    "source_record_id",
    "geometry_method",
    "geometry",
)

VOLTAGE_DTYPE = pd.ArrowDtype(pa.list_(pa.float64()))


@dataclass(frozen=True)
class NetworkData:
    """Canonical OSM connectivity represented by node and branch tables."""

    nodes: gpd.GeoDataFrame
    branches: gpd.GeoDataFrame


def _string_series(values: object, index: pd.Index) -> pd.Series:
    if isinstance(values, pd.Series):
        return values.reindex(index).astype("string")
    return pd.Series(values, index=index, dtype="string")


def _voltage_series(values: Iterable[object], index: pd.Index) -> pd.Series:
    normalized = []
    for value in values:
        if value is None or value is pd.NA:
            normalized.append(None)
        elif isinstance(value, (list, tuple, np.ndarray)):
            numbers = sorted({
                float(number) for number in value if pd.notna(number)
            })
            normalized.append(numbers or None)
        else:
            normalized.append([float(value)] if pd.notna(value) else None)
    return pd.Series(normalized, index=index, dtype=VOLTAGE_DTYPE)


def _partial_time(value: object) -> object:
    """Preserve the source precision as a partial ISO-8601 string."""

    if pd.isna(value) or str(value).strip() in {"", "nan", "NaT"}:
        return pd.NA
    if isinstance(value, (int, np.integer)):
        return f"{int(value):04d}"
    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        return f"{int(value):04d}"
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        timestamp = pd.Timestamp(value)
        return timestamp.isoformat()
    return str(value).strip()


def time_bounds(value: object) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return the half-open interval represented by a partial ISO time."""

    text = str(value).strip()
    if re.fullmatch(r"\d{4}", text):
        start = pd.Timestamp(f"{text}-01-01", tz="UTC")
        return start, start + pd.DateOffset(years=1)
    if re.fullmatch(r"\d{4}-\d{2}", text):
        start = pd.Timestamp(f"{text}-01", tz="UTC")
        return start, start + pd.DateOffset(months=1)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        start = pd.Timestamp(text, tz="UTC")
        return start, start + pd.Timedelta(days=1)
    start = pd.Timestamp(text)
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    return start, start + pd.Timedelta(seconds=1)


def _nullable_boolean(values: pd.Series) -> pd.Series:
    normalized = values.fillna("").astype(str).str.strip().str.lower()
    result = pd.Series(pd.NA, index=values.index, dtype="boolean")
    result.loc[normalized.isin({"yes", "true", "1"})] = True
    result.loc[normalized.isin({"no", "false", "0"})] = False
    return result


def _snake_case(value: object) -> object:
    if pd.isna(value) or not str(value).strip():
        return pd.NA
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", str(value).lower())).strip("_")


def _osm_voltages(value: object) -> list[float]:
    if pd.isna(value):
        return []
    volts = [
        float(number)
        for number in re.findall(r"\d+(?:\.\d+)?", str(value))
        if float(number) >= 1000
    ]
    return sorted({number / 1000 for number in volts})


def _numeric(value: object) -> object:
    number = pd.to_numeric(value, errors="coerce")
    return pd.NA if pd.isna(number) else float(number)


def _points(longitude: pd.Series, latitude: pd.Series) -> list[Point | None]:
    return [
        Point(float(x), float(y))
        if pd.notna(x) and pd.notna(y) and -180 <= x <= 180 and -90 <= y <= 90
        else None
        for x, y in zip(longitude, latitude, strict=True)
    ]


def _finalize_entities(
    frame: pd.DataFrame,
    *,
    crs: str = "EPSG:4326",
    extra_columns: Iterable[str] = (),
    string_columns: Iterable[str] = (),
) -> gpd.GeoDataFrame:
    frame = frame.copy()
    for column in ENTITY_COLUMNS:
        if column not in frame:
            frame[column] = None if column == "geometry" else pd.NA
    for column in (
        "uid",
        "type",
        "technology",
        "status",
        "valid_from",
        "valid_to",
        "observed_at",
        "source_id",
        "source_record_id",
        "geometry_method",
    ):
        frame[column] = _string_series(frame[column], frame.index)
    for column in string_columns:
        frame[column] = _string_series(frame[column], frame.index)
    frame["voltage_kv"] = _voltage_series(frame["voltage_kv"], frame.index)
    result = gpd.GeoDataFrame(frame, geometry="geometry", crs=crs)
    if result["uid"].isna().any() or result["uid"].duplicated().any():
        raise ValueError("Entity uid values must be present and unique.")
    if result["type"].isna().any():
        raise ValueError("Entity type values must be present.")
    ordered = [*ENTITY_COLUMNS[:-1], *extra_columns, "geometry"]
    return result.loc[:, list(dict.fromkeys(ordered))]


def _write_geodataframe(frame: gpd.GeoDataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _read_geodataframe(path: Path) -> gpd.GeoDataFrame:
    return gpd.read_parquet(
        path,
        to_pandas_kwargs={"types_mapper": pd.ArrowDtype},
    )
