"""Live schema inspection for materialized mapping products."""

from __future__ import annotations

from dataclasses import fields

import pandas as pd
import xarray as xr

from ..standard import StandardNetwork
from ..standard.schema import _SCHEMA_COLUMNS, _dataset_schema
from .model import MappedData, MappedNetwork


_DESCRIPTIONS = {
    "spatial_uid": "UID of the standard spatial cell.",
    "admin_uid": "UID of the spatial unit used to clip the cell.",
    "spatial_level": "Level of the spatial unit used to clip the cell.",
    "centre_geometry": "Representative centre point of the standard cell.",
    "area_km2": "Area of the clipped standard cell in square kilometres.",
    "cell_kind": "Geometry family used to construct the standard cells.",
    "source_cell_uid": "UID of the unclipped source cell.",
    "mapping_dataset_id": "Stable mapped-data identifier.",
    "cell_distance_km": "Distance used when assigning an object to a cell.",
    "bus_uid": "UID of the selected electrical-network bus.",
    "bus_mapping_method": "Geometry- or cell-based bus mapping method.",
    "bus_distance_km": "Distance from the source object to the selected bus.",
    "bus_same_admin": "Whether the source object and bus share an admin UID.",
    "bus_spatial_uid": "Standard-cell UID assigned to the selected bus.",
    "bus_admin_uid": "Administrative UID assigned to the selected bus.",
    "branch_uid": "UID of the mapped network branch.",
    "overlap_length_km": "Branch length contained in the standard cell.",
    "branch_length_share": "Share of total branch length contained in the cell.",
    "mapping_status": "Whether the branch intersects the spatial-cell domain.",
    "in_largest_connected_graph": "Whether the object is in the retained network.",
}

_REQUIRED = {
    ("spatial", "data"): {
        "spatial_uid", "admin_uid", "spatial_level", "geometry",
        "centre_geometry", "area_km2",
    },
    ("population", "data"): {
        "spatial_uid", "admin_uid", "spatial_level", "geometry",
        "centre_geometry", "area_km2", "population",
    },
    ("load", "data"): {
        "spatial_uid", "spatial_level", "bus_uid", "bus_mapping_method", "bus_distance_km",
        "bus_same_admin", "bus_spatial_uid", "bus_admin_uid",
    },
    ("resource", "data"): {"spatial_uid", "spatial_level"},
    ("generator", "data"): {
        "spatial_uid", "spatial_level", "admin_uid", "cell_distance_km", "bus_uid",
        "bus_mapping_method", "bus_distance_km", "bus_same_admin",
        "bus_spatial_uid", "bus_admin_uid",
    },
    ("storage", "data"): {
        "spatial_uid", "spatial_level", "admin_uid", "cell_distance_km", "bus_uid",
        "bus_mapping_method", "bus_distance_km", "bus_same_admin",
        "bus_spatial_uid", "bus_admin_uid",
    },
    ("network", "branch_mapping"): {
        "branch_uid", "spatial_uid", "spatial_level", "admin_uid", "overlap_length_km",
        "branch_length_share", "mapping_status",
    },
    ("network", "bus"): {
        "spatial_uid", "spatial_level", "admin_uid", "cell_distance_km",
        "in_largest_connected_graph",
    },
    ("network", "branch"): {"in_largest_connected_graph"},
    ("network", "transformer"): {"in_largest_connected_graph"},
    ("network", "converter"): {"in_largest_connected_graph"},
}


def annotate_schema(
    data: object,
    dataset_id: str,
    component: str = "data",
) -> object:
    """Attach live schema requirements without adding data columns."""

    required = tuple(_REQUIRED.get((dataset_id, component), ()))
    if isinstance(data, xr.Dataset):
        data.attrs["_schema_required_coordinates"] = required
        data.attrs["_schema_required_attributes"] = ("mapping_dataset_id",)
    elif isinstance(data, pd.DataFrame):
        data.attrs["_schema_required_columns"] = required
        data.attrs["_schema_required_attributes"] = ("mapping_dataset_id",)
    return data


def mapping_schema(
    data: object,
    dataset_id: str | None = None,
) -> pd.DataFrame:
    """Describe actual structures contained in one or more mapped products."""

    if isinstance(data, MappedData):
        tables = []
        for field in fields(data):
            if field.name == "config":
                continue
            tables.append(mapping_schema(getattr(data, field.name), field.name))
        return _finish(pd.concat(tables, ignore_index=True))

    if isinstance(data, MappedNetwork):
        table = pd.concat([
            _component_schema(StandardNetwork(
                data.bus, data.branch, data.transformer, data.converter
            ), "network"),
            _component_schema(data.branch_mapping, "branch_mapping"),
        ], ignore_index=True)
        table.insert(0, "dataset_id", dataset_id or "network")
        return _finish(table)

    table = _component_schema(data, "data")
    table.insert(0, "dataset_id", dataset_id or pd.NA)
    return _finish(table)


def _component_schema(data: object, component: str) -> pd.DataFrame:
    table = _dataset_schema(data).copy()
    if component != "network":
        table["component"] = component
    return table


def _finish(table: pd.DataFrame) -> pd.DataFrame:
    if "dataset_id" not in table:
        table.insert(0, "dataset_id", pd.NA)
    keys = list(zip(table["dataset_id"], table["component"]))
    required = [
        name == "mapping_dataset_id" or name in _REQUIRED.get(key, set())
        for key, name in zip(keys, table["name"])
    ]
    table["required"] = table["required"].fillna(False) | pd.Series(
        required,
        index=table.index,
        dtype="boolean",
    )
    missing = table["description"].isna()
    table.loc[missing, "description"] = table.loc[missing, "name"].map(
        _DESCRIPTIONS
    )
    return table[["dataset_id", *_SCHEMA_COLUMNS]]


class SchemaAccessor:
    """Expose a live schema as both ``.schema`` and ``.schema()``."""

    def __init__(self, data: object) -> None:
        self._data = data

    @property
    def table(self) -> pd.DataFrame:
        return mapping_schema(self._data)

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
