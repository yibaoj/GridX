"""Reuse mapping-layer functions against a case-filtered network."""

from __future__ import annotations

import geopandas as gpd
import xarray as xr

from ..mapping.network import (
    attach_bus_coordinates,
    attach_bus_mapping,
    map_to_buses,
)


_BUS_COLUMNS = (
    "bus_uid", "bus_mapping_method", "bus_distance_km", "bus_same_admin",
    "bus_spatial_uid", "bus_admin_uid",
)


def remap_assets(
    data: gpd.GeoDataFrame,
    buses: gpd.GeoDataFrame,
    cells: gpd.GeoDataFrame,
    *,
    dataset_id: str,
    options: dict,
    metric_crs: str,
    random_seed: int,
    bus_subclasses: list[str],
) -> gpd.GeoDataFrame:
    """Remap generator or storage records to retained buses."""

    source = data.drop(columns=list(_BUS_COLUMNS), errors="ignore")
    mapping = map_to_buses(
        source,
        buses,
        cells,
        source_uid_column="uid",
        output_uid_column=f"{dataset_id}_uid",
        method=str(options["method"]),
        prefer_same_admin=bool(options["prefer_same_admin"]),
        metric_crs=metric_crs,
        random_seed=random_seed,
        bus_subclasses=bus_subclasses,
        voltage_preference=str(options["voltage_preference"]),
    )
    return attach_bus_mapping(
        source, mapping, source_uid_column=f"{dataset_id}_uid"
    )


def remap_load(
    data: xr.Dataset,
    buses: gpd.GeoDataFrame,
    cells: gpd.GeoDataFrame,
    *,
    options: dict,
    metric_crs: str,
    random_seed: int,
    bus_subclasses: list[str],
) -> xr.Dataset:
    """Remap each load cell to a retained bus."""

    source = cells.loc[cells["spatial_uid"].isin(data["uid"].values)].copy()
    source["geometry"] = source["centre_geometry"]
    source = source.set_geometry("geometry")
    mapping = map_to_buses(
        source,
        buses,
        cells,
        source_uid_column="spatial_uid",
        output_uid_column="load_spatial_uid",
        method=str(options["method"]),
        prefer_same_admin=bool(options["prefer_same_admin"]),
        metric_crs=metric_crs,
        random_seed=random_seed,
        bus_subclasses=bus_subclasses,
        voltage_preference=str(options["voltage_preference"]),
    )
    return attach_bus_coordinates(
        data, mapping, source_uid_column="load_spatial_uid"
    )
