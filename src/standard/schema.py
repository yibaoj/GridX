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
import pyarrow.parquet as pq
import xarray as xr
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

__all__ = [
    "DATASET_IDS",
    "REQUIRED_ATTRIBUTES",
    "REQUIRED_COLUMNS",
    "NetworkData",
    "time_bounds",
]

_SCHEMA_COLUMNS = (
    "component",
    "role",
    "name",
    "dimensions",
    "dtype",
    "size",
    "required",
    "description",
    "value",
)

REQUIRED_COLUMNS = {
    "spatial": (
        "uid", "level", "geometry", "geometry_method", "observed_at",
        "valid_from", "valid_to", "source_id", "source_uid",
    ),
    "generation": (
        "uid", "class", "subclass", "status", "capacity_mw", "fuel",
        "chp", "ccs", "voltage_kv", "geometry", "geometry_method",
        "observed_at", "valid_from", "valid_to", "source_id", "source_uid",
        "mapping_rule_id",
    ),
    "storage": (
        "uid", "class", "subclass", "status", "power_capacity_mw",
        "energy_capacity_mwh", "duration_h", "voltage_kv", "geometry",
        "geometry_method", "observed_at", "valid_from", "valid_to",
        "source_id", "source_uid", "mapping_rule_id",
    ),
    "network.bus": (
        "uid", "class", "subclass", "status", "voltage_kv", "current_type",
        "geometry", "geometry_method", "observed_at", "valid_from", "valid_to",
        "source_id", "source_uid",
    ),
    "network.branch": (
        "uid", "class", "subclass", "status", "voltage_kv", "current_type",
        "from_bus_uid", "to_bus_uid", "length_km", "geometry", "geometry_method", "observed_at",
        "valid_from", "valid_to", "source_id", "source_uid",
    ),
    "network.transformer": (
        "uid", "class", "subclass", "status", "from_bus_uid", "to_bus_uid",
        "from_voltage_kv", "to_voltage_kv", "geometry", "geometry_method",
        "observed_at", "valid_from", "valid_to", "source_id", "source_uid",
    ),
    "network.converter": (
        "uid", "class", "subclass", "status", "from_bus_uid", "to_bus_uid",
        "from_voltage_kv", "to_voltage_kv", "from_current_type", "to_current_type",
        "geometry", "geometry_method", "observed_at", "valid_from", "valid_to",
        "source_id", "source_uid",
    ),
    # Internal geographic connectivity used before electrical bus conversion.
    "network.nodes": (
        "uid", "class", "subclass", "status", "voltage_kv", "geometry",
        "geometry_method", "observed_at", "valid_from", "valid_to",
        "source_id", "source_uid",
    ),
    "network.branches": (
        "uid", "class", "subclass", "status", "voltage_kv", "from_uid",
        "to_uid", "length_km", "geometry", "geometry_method", "observed_at",
        "valid_from", "valid_to", "source_id", "source_uid",
    ),
    "population": (
        "uid", "class", "population", "geometry", "geometry_method",
        "observed_at", "valid_from", "valid_to", "source_id", "source_uid",
    ),
    "parameter": (
        "uid", "name", "group", "value", "unit",
        "applies_to_dataset", "applies_to_uid", "class", "subclass", "status",
        "observed_at", "valid_from", "valid_to", "source_id", "source_uid",
        "source_version",
    ),
}

REQUIRED_ATTRIBUTES = {
    schema_id: ("standard_dataset_id", "crs")
    for schema_id in (*REQUIRED_COLUMNS, "load", "resource")
}

XARRAY_SCHEMAS = {
    "load": {
        "variable": "demand_mw",
        "dimensions": ("time", "uid", "class"),
        "coordinates": (
            "time", "uid", "class", "location", "geometry", "geometry_method",
        ),
        "attributes": (
            *REQUIRED_ATTRIBUTES["load"], "timezone", "time_step",
            "source_unit", "unit", "source_id",
        ),
    },
    "resource": {
        "variable": "availability_pu",
        "dimensions": ("time", "uid", "class"),
        "coordinates": (
            "time", "uid", "class", "location", "geometry", "geometry_method",
        ),
        "attributes": (
            *REQUIRED_ATTRIBUTES["resource"], "timezone", "time_step",
            "source_unit", "unit", "source_id",
        ),
    },
}

FIELD_DESCRIPTIONS = {
    "uid": "Globally unique identifier for the standardized record.",
    "standard_dataset_id": "Stable identifier of the standardized dataset.",
    "class": "Primary standardized category.",
    "subclass": "More specific, extensible standardized category.",
    "level": "Spatial-unit level, such as province, city, or marine_zone.",
    "status": "Lifecycle or operating status.",
    "capacity_mw": "Installed generation capacity in MW.",
    "power_capacity_mw": "Storage charge/discharge power capacity in MW.",
    "energy_capacity_mwh": "Storage energy capacity in MWh.",
    "duration_h": "Storage duration in hours.",
    "population": "Population represented by the geometry.",
    "fuel": "Normalized fuel label.",
    "chp": "Whether the generation asset provides combined heat and power.",
    "ccs": "Whether the generation asset uses carbon capture and storage.",
    "voltage_kv": "Voltage level in kV; electrical network components use one scalar level.",
    "current_type": "Electrical current type: AC or DC.",
    "from_bus_uid": "UID of the first connected electrical bus; direction is topological only.",
    "to_bus_uid": "UID of the second connected electrical bus; direction is topological only.",
    "from_voltage_kv": "Voltage level of the first connected bus in kV.",
    "to_voltage_kv": "Voltage level of the second connected bus in kV.",
    "from_current_type": "Current type of the first connected bus.",
    "to_current_type": "Current type of the second connected bus.",
    "from_uid": "UID of the internal geographic start node.",
    "to_uid": "UID of the internal geographic end node.",
    "length_km": "Projected network-feature length in kilometres.",
    "voltage_raw": "Unmodified voltage tag from the source network feature.",
    "voltage_assignment_method": "Rule used to preserve or infer voltage levels.",
    "voltage_inferred": "Whether any voltage level was inferred rather than directly sourced.",
    "voltage_reference_uid": "Source network object used for voltage inference.",
    "circuits_raw": "Unmodified circuits tag from the source network feature.",
    "cables_raw": "Unmodified cables tag from the source network feature.",
    "circuit_allocation_method": "Rule used to allocate source circuits by voltage.",
    "cable_allocation_method": "Rule used to allocate source cables by voltage.",
    "circuit_inferred": "Whether the per-voltage circuit count was inferred.",
    "cable_inferred": "Whether the per-voltage cable count was inferred.",
    "geometry": "Canonical geometry in the dataset CRS.",
    "geometry_method": "Method used to obtain or infer the geometry.",
    "observed_at": "Time at which the source record was observed.",
    "valid_from": "Inclusive start of the record's validity.",
    "valid_to": "End of the record's validity.",
    "source_id": "Raw-data catalog identifier.",
    "source_uid": "Identifier of the originating source record.",
    "mapping_rule_id": "Classification rule that produced class and subclass.",
    "applies_to_dataset": "Dataset to which the parameter applies.",
    "applies_to_uid": "Optional UID of the specific object to which it applies.",
    "name": "Stable canonical parameter name used across data, case, and application layers.",
    "group": "Parameter family: technical, economic, environmental, or others.",
    "value": "Numeric parameter value in the stated unit.",
    "source_name": "Unmodified parameter name used by the source.",
    "selector_json": "Optional structured matching conditions beyond the common selector columns.",
    "scenario": "Optional scenario to which the parameter applies.",
    "priority": "Explicit resolver priority; smaller values are preferred.",
    "is_derived": "Whether the value was calculated or inferred from another source value.",
    "derivation": "Auditable explanation of how a derived value was calculated.",
    "quality": "Source, derivation, or proxy quality label.",
    "notes": "Qualification needed to interpret or apply the parameter.",
    "reference_url": "Publication or repository URL supporting the parameter.",
    "source_provider": "Institution or project that supplied the parameter.",
    "scope": "Geographic, technological, or system scope of the parameter.",
    "standard_type": "Named equipment standard type used by the source library.",
    "pypsa_technology": "PyPSA technology-data technology label.",
    "fuel_technology": "Fuel label used to associate fuel and emission parameters.",
    "currency_year": "Price year used for monetary values.",
    "source_version": "Version, release, or commit of the upstream parameter source.",
    "time": "Timestamp coordinate.",
    "location": "Human-readable location associated with uid.",
    "timezone": "Timezone used to interpret the time coordinate.",
    "time_step": "Nominal interval between consecutive timestamps.",
    "source_unit": "Unit used by the raw source.",
    "unit": "Canonical unit of the data variable.",
    "crs": "Coordinate reference system used by geometry values.",
    "demand_mw": "Electrical demand in MW.",
    "availability_pu": "Resource availability per unit of installed capacity.",
    "spatial_uid": "UID of the standard spatial cell.",
    "spatial_level": "Level of the spatial unit used to clip the cell.",
    "admin_uid": "UID of the spatial unit used to clip the cell.",
    "centre_geometry": "Representative centre point of the standard cell.",
    "area_km2": "Area of the clipped standard cell in square kilometres.",
    "cell_kind": "Geometry family used to construct the standard cells.",
    "source_cell_uid": "UID of the unclipped source cell.",
    "cell_distance_km": "Distance used when assigning an object to a cell.",
    "bus_uid": "UID of the selected electrical-network bus.",
    "bus_mapping_method": "Geometry- or cell-based bus mapping method.",
    "bus_distance_km": "Distance from the source object to the selected bus.",
    "bus_same_admin": "Whether the source object and bus share an admin UID.",
    "bus_spatial_uid": "Standard-cell UID assigned to the selected bus.",
    "bus_admin_uid": "Administrative UID assigned to the selected bus.",
    "mapping_dataset_id": "Stable mapped-data identifier.",
    "in_largest_connected_graph": "Whether the object is in the retained network.",
}

VOLTAGE_DTYPE = pd.ArrowDtype(pa.list_(pa.float64()))

_STRING_FIELDS = {
    "uid", "class", "subclass", "level", "status", "fuel",
    "geometry_method", "observed_at", "valid_from", "valid_to", "source_id",
    "source_uid", "mapping_rule_id", "from_uid", "to_uid",
    "from_bus_uid", "to_bus_uid",
    "current_type", "from_current_type", "to_current_type",
    "applies_to_dataset", "applies_to_uid", "name", "group", "unit",
    "source_version",
}
_NUMERIC_FIELDS = {
    "capacity_mw", "power_capacity_mw", "energy_capacity_mwh", "duration_h",
    "length_km", "population", "value", "capacity_min_mw", "capacity_max_mw",
    "priority", "from_voltage_kv", "to_voltage_kv",
}
_BOOLEAN_FIELDS = {
    "chp", "ccs", "voltage_inferred", "circuit_inferred", "cable_inferred",
    "is_derived", "inferred",
}


@dataclass(frozen=True)
class NetworkData:
    """Canonical electrical network represented by four component tables."""

    bus: gpd.GeoDataFrame
    branch: gpd.GeoDataFrame
    transformer: gpd.GeoDataFrame
    converter: gpd.GeoDataFrame

    @property
    def schema(self) -> "_SchemaAccessor":
        """Return the schema accessor for all electrical component tables."""

        return _SchemaAccessor(self)


def _dataset_schema(data: object) -> pd.DataFrame:
    """Describe the structure of one materialized standard dataset."""

    if isinstance(data, NetworkData):
        rows = [
            *_frame_schema_rows(data.bus, "bus", "network.bus"),
            *_frame_schema_rows(data.branch, "branch", "network.branch"),
            *_frame_schema_rows(data.transformer, "transformer", "network.transformer"),
            *_frame_schema_rows(data.converter, "converter", "network.converter"),
        ]
    elif isinstance(data, pd.DataFrame):
        rows = _frame_schema_rows(
            data,
            "data",
            data.attrs.get("standard_dataset_id") or _infer_frame_schema(data),
        )
    elif isinstance(data, xr.Dataset):
        rows = _xarray_schema_rows(data)
    else:
        raise TypeError(
            "Schema inspection supports pandas/GeoPandas DataFrames, "
            "xarray Datasets, and NetworkData."
        )

    result = pd.DataFrame(rows, columns=_SCHEMA_COLUMNS)
    for column in ("component", "role", "name", "dimensions", "dtype"):
        result[column] = result[column].astype("string")
    result["size"] = pd.to_numeric(result["size"], errors="coerce").astype("Int64")
    result["required"] = result["required"].astype("boolean")
    return result


def _frame_schema_rows(
    frame: pd.DataFrame,
    component: str,
    schema_id: str | None,
) -> list[dict[str, object]]:
    required = set(REQUIRED_COLUMNS.get(schema_id or "", ()))
    required.update(frame.attrs.get("_schema_required_columns", ()))
    required_attributes = set(REQUIRED_ATTRIBUTES.get(schema_id or "", ()))
    required_attributes.update(
        frame.attrs.get("_schema_required_attributes", ())
    )
    rows = [{
        "component": component,
        "role": "dimension",
        "name": "row",
        "dimensions": pd.NA,
        "dtype": pd.NA,
        "size": len(frame),
        "required": False,
        "description": "Number of records in the table.",
        "value": pd.NA,
    }]
    rows.extend({
        "component": component,
        "role": "column",
        "name": column,
        "dimensions": "row",
        "dtype": str(dtype),
        "size": len(frame),
        "required": column in required,
        "description": FIELD_DESCRIPTIONS.get(column, pd.NA),
        "value": pd.NA,
    } for column, dtype in frame.dtypes.items())
    if hasattr(frame, "crs"):
        rows.append({
            "component": component,
            "role": "attribute",
            "name": "crs",
            "dimensions": pd.NA,
            "dtype": type(frame.crs).__name__,
            "size": pd.NA,
            "required": "crs" in required_attributes,
            "description": FIELD_DESCRIPTIONS["crs"],
            "value": frame.crs.to_string(),
        })
    rows.extend({
        "component": component,
        "role": "attribute",
        "name": name,
        "dimensions": pd.NA,
        "dtype": type(value).__name__,
        "size": pd.NA,
        "required": name in required_attributes,
        "description": FIELD_DESCRIPTIONS.get(name, pd.NA),
        "value": value,
    } for name, value in frame.attrs.items() if not name.startswith("_schema_"))
    return rows


def _xarray_schema_rows(data: xr.Dataset) -> list[dict[str, object]]:
    dataset_id = _infer_xarray_schema(data)
    schema = XARRAY_SCHEMAS.get(dataset_id or "", {})
    required_dimensions = set(schema.get("dimensions", ()))
    required_coordinates = set(schema.get("coordinates", ()))
    required_attributes = set(schema.get("attributes", ()))
    required_coordinates.update(
        data.attrs.get("_schema_required_coordinates", ())
    )
    required_attributes.update(
        data.attrs.get("_schema_required_attributes", ())
    )
    rows = [{
        "component": "data",
        "role": "dimension",
        "name": name,
        "dimensions": pd.NA,
        "dtype": pd.NA,
        "size": size,
        "required": name in required_dimensions,
        "description": FIELD_DESCRIPTIONS.get(name, pd.NA),
        "value": pd.NA,
    } for name, size in data.sizes.items()]
    rows.extend({
        "component": "data",
        "role": "coordinate",
        "name": name,
        "dimensions": ", ".join(array.dims),
        "dtype": str(array.dtype),
        "size": array.size,
        "required": name in required_coordinates,
        "description": FIELD_DESCRIPTIONS.get(name, pd.NA),
        "value": pd.NA,
    } for name, array in data.coords.items())
    rows.extend({
        "component": "data",
        "role": "data_variable",
        "name": name,
        "dimensions": ", ".join(array.dims),
        "dtype": str(array.dtype),
        "size": array.size,
        "required": name == schema.get("variable"),
        "description": FIELD_DESCRIPTIONS.get(name, pd.NA),
        "value": pd.NA,
    } for name, array in data.data_vars.items())
    rows.extend({
        "component": "data",
        "role": "attribute",
        "name": name,
        "dimensions": pd.NA,
        "dtype": type(value).__name__,
        "size": pd.NA,
        "required": name in required_attributes,
        "description": FIELD_DESCRIPTIONS.get(name, pd.NA),
        "value": value,
    } for name, value in data.attrs.items() if not name.startswith("_schema_"))
    return rows


def _infer_frame_schema(frame: pd.DataFrame) -> str | None:
    columns = set(frame.columns)
    matches = [
        schema_id
        for schema_id, required in REQUIRED_COLUMNS.items()
        if set(required).issubset(columns)
    ]
    return max(matches, key=lambda item: len(REQUIRED_COLUMNS[item])) if matches else None


def _infer_xarray_schema(data: xr.Dataset) -> str | None:
    for dataset_id, schema in XARRAY_SCHEMAS.items():
        if schema["variable"] in data:
            return dataset_id
    return None


class _SchemaAccessor:
    """Render a live schema table for a loaded pandas or xarray object."""

    def __init__(self, data: object) -> None:
        self._data = data

    @property
    def table(self) -> pd.DataFrame:
        return _dataset_schema(self._data)

    def __call__(self) -> pd.DataFrame:
        return self.table

    def __repr__(self) -> str:
        return repr(self.table)

    def _repr_html_(self) -> str:
        return self.table._repr_html_()

    def __getattr__(self, name: str) -> object:
        return getattr(self.table, name)

    def __getitem__(self, key: object) -> object:
        return self.table[key]


pd.api.extensions.register_dataframe_accessor("schema")(_SchemaAccessor)
xr.register_dataset_accessor("schema")(_SchemaAccessor)


def _string_series(values: object, index: pd.Index) -> pd.Series:
    if isinstance(values, pd.Series):
        return values.reindex(index).astype("string")
    return pd.Series(values, index=index, dtype="string")


def _is_string_dtype(dtype: object) -> bool:
    return isinstance(dtype, pd.StringDtype) or (
        isinstance(dtype, pd.ArrowDtype)
        and (
            pa.types.is_string(dtype.pyarrow_dtype)
            or pa.types.is_large_string(dtype.pyarrow_dtype)
        )
    )


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


def _finalize_frame(
    frame: pd.DataFrame,
    *,
    schema_id: str,
    crs: str = "EPSG:4326",
    string_columns: Iterable[str] = (),
) -> pd.DataFrame:
    frame = frame.copy()
    required = REQUIRED_COLUMNS[schema_id]
    missing = set(required).difference(frame.columns)
    if missing:
        raise ValueError(f"{schema_id} is missing required columns: {sorted(missing)}")
    for column in _STRING_FIELDS:
        if column in frame:
            frame[column] = _string_series(frame[column], frame.index)
    for column in string_columns:
        frame[column] = _string_series(frame[column], frame.index)
    if "voltage_kv" in frame:
        if schema_id in {"network.bus", "network.branch"}:
            frame["voltage_kv"] = pd.to_numeric(
                frame["voltage_kv"], errors="coerce"
            ).astype("Float64")
        else:
            frame["voltage_kv"] = _voltage_series(frame["voltage_kv"], frame.index)
    for column in _NUMERIC_FIELDS.intersection(frame.columns):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Float64")
    for column in _BOOLEAN_FIELDS.intersection(frame.columns):
        frame[column] = frame[column].astype("boolean")
    result = (
        gpd.GeoDataFrame(frame, geometry="geometry", crs=crs)
        if "geometry" in frame
        else frame
    )
    if result["uid"].isna().any() or result["uid"].duplicated().any():
        raise ValueError(f"{schema_id} uid values must be present and unique.")
    ordered = [*required, *(column for column in result if column not in required)]
    result = result.loc[:, ordered]
    result.attrs["standard_dataset_id"] = schema_id.split(".", 1)[0]
    if not isinstance(result, gpd.GeoDataFrame):
        result.attrs["crs"] = crs
    _validate_frame(result, schema_id)
    return result


def _validate_frame(frame: pd.DataFrame, schema_id: str) -> None:
    required = REQUIRED_COLUMNS[schema_id]
    missing = set(required).difference(frame.columns)
    if missing:
        raise ValueError(f"{schema_id} is missing required columns: {sorted(missing)}")
    if tuple(frame.columns[: len(required)]) != required:
        raise ValueError(f"{schema_id} required columns are not in canonical order.")
    if frame["uid"].isna().any() or frame["uid"].duplicated().any():
        raise ValueError(f"{schema_id} uid values must be present and unique.")
    dataset_id = schema_id.split(".", 1)[0]
    if frame.attrs.get("standard_dataset_id") != dataset_id:
        raise ValueError(
            f"{schema_id}.attrs['standard_dataset_id'] must be {dataset_id!r}."
        )
    frame_crs = (
        frame.crs
        if isinstance(frame, gpd.GeoDataFrame)
        else frame.attrs.get("crs")
    )
    if frame_crs is None:
        raise ValueError(f"{schema_id} must define a crs attribute.")
    if "geometry" in required and not isinstance(frame, gpd.GeoDataFrame):
        raise TypeError(f"{schema_id} must be a GeoDataFrame.")
    for column in _STRING_FIELDS.intersection(required):
        if not _is_string_dtype(frame[column].dtype):
            raise TypeError(f"{schema_id}.{column} must use a nullable string dtype.")
    for column in _NUMERIC_FIELDS.intersection(required):
        if not pd.api.types.is_numeric_dtype(frame[column].dtype):
            raise TypeError(f"{schema_id}.{column} must be numeric.")
    for column in _BOOLEAN_FIELDS.intersection(required):
        if not pd.api.types.is_bool_dtype(frame[column].dtype):
            raise TypeError(f"{schema_id}.{column} must be nullable boolean.")
    if "voltage_kv" in required and schema_id in {"network.bus", "network.branch"}:
        if not pd.api.types.is_numeric_dtype(frame["voltage_kv"].dtype):
            raise TypeError(f"{schema_id}.voltage_kv must be numeric.")
    elif "voltage_kv" in required:
        dtype = frame["voltage_kv"].dtype
        if not (
            isinstance(dtype, pd.ArrowDtype)
            and (
                pa.types.is_list(dtype.pyarrow_dtype)
                or pa.types.is_large_list(dtype.pyarrow_dtype)
            )
        ):
            raise TypeError(f"{schema_id}.voltage_kv must use an Arrow list dtype.")


def _validate_xarray(data: xr.Dataset, dataset_id: str) -> None:
    schema = XARRAY_SCHEMAS[dataset_id]
    variable = schema["variable"]
    if tuple(data.sizes) != schema["dimensions"]:
        raise ValueError(
            f"{dataset_id} dimensions must be {schema['dimensions']}, "
            f"not {tuple(data.sizes)}."
        )
    if set(data.coords) != set(schema["coordinates"]):
        raise ValueError(
            f"{dataset_id} coordinates must be {schema['coordinates']}, "
            f"not {tuple(data.coords)}."
        )
    if set(data.data_vars) != {variable}:
        raise ValueError(f"{dataset_id} must contain only data variable {variable!r}.")
    if data[variable].dims != schema["dimensions"]:
        raise ValueError(f"{dataset_id}.{variable} has invalid dimensions.")
    if not pd.api.types.is_numeric_dtype(data[variable].dtype):
        raise TypeError(f"{dataset_id}.{variable} must be numeric.")
    if not pd.api.types.is_datetime64_any_dtype(data["time"].dtype):
        raise TypeError(f"{dataset_id}.time must be datetime64.")
    for coordinate in ("location", "geometry", "geometry_method"):
        if data[coordinate].dims != ("uid",):
            raise ValueError(f"{dataset_id}.{coordinate} must be indexed only by uid.")
    if set(data.attrs) != set(schema["attributes"]):
        raise ValueError(
            f"{dataset_id} attributes must be {schema['attributes']}, "
            f"not {tuple(data.attrs)}."
        )
    if data.attrs["standard_dataset_id"] != dataset_id:
        raise ValueError(
            f"{dataset_id}.attrs['standard_dataset_id'] must be {dataset_id!r}."
        )


def _validate_dataset(data: object, dataset_id: str) -> None:
    if dataset_id == "network":
        if not isinstance(data, NetworkData):
            raise TypeError("network must be represented by NetworkData.")
        _validate_frame(data.bus, "network.bus")
        _validate_frame(data.branch, "network.branch")
        _validate_frame(data.transformer, "network.transformer")
        _validate_frame(data.converter, "network.converter")
    elif dataset_id in XARRAY_SCHEMAS:
        if not isinstance(data, xr.Dataset):
            raise TypeError(f"{dataset_id} must be an xarray.Dataset.")
        _validate_xarray(data, dataset_id)
    else:
        if not isinstance(data, pd.DataFrame):
            raise TypeError(f"{dataset_id} must be a DataFrame.")
        _validate_frame(data, dataset_id)


def _write_geodataframe(frame: gpd.GeoDataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    _write_parquet_dataset_id(path, frame.attrs["standard_dataset_id"])


def _read_geodataframe(path: Path) -> gpd.GeoDataFrame:
    frame = gpd.read_parquet(
        path,
        to_pandas_kwargs={"types_mapper": pd.ArrowDtype},
    )
    frame.attrs["standard_dataset_id"] = _read_parquet_dataset_id(path)
    return frame


def _write_dataframe(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    _write_parquet_attributes(
        path,
        standard_dataset_id=frame.attrs["standard_dataset_id"],
        crs=frame.attrs["crs"],
    )


def _write_xarray(data: xr.Dataset, path: Path, variable: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    data.to_netcdf(
        temporary_path,
        encoding={variable: {"zlib": True, "complevel": 4}},
    )
    temporary_path.replace(path)


def _read_dataframe(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path, dtype_backend="pyarrow")
    frame.attrs["standard_dataset_id"] = _read_parquet_dataset_id(path)
    frame.attrs["crs"] = _read_parquet_attribute(path, "crs")
    return frame


def _write_parquet_dataset_id(path: Path, dataset_id: str) -> None:
    _write_parquet_attributes(path, standard_dataset_id=dataset_id)


def _write_parquet_attributes(path: Path, **attributes: object) -> None:
    table = pq.read_table(path)
    metadata = dict(table.schema.metadata or {})
    metadata.update(
        {name.encode(): str(value).encode() for name, value in attributes.items()}
    )
    pq.write_table(table.replace_schema_metadata(metadata), path)


def _read_parquet_dataset_id(path: Path) -> str:
    return _read_parquet_attribute(path, "standard_dataset_id")


def _read_parquet_attribute(path: Path, name: str) -> str:
    value = (pq.read_metadata(path).metadata or {}).get(name.encode())
    if value is None:
        raise ValueError(f"Parquet metadata has no {name}: {path}")
    return value.decode()
