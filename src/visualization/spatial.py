"""Spatial helpers shared by mapped and case plotting adapters."""

from __future__ import annotations

import warnings

import geopandas as gpd


def spatial_background(cells: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Recover administrative display boundaries from mapped cells."""

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="invalid value encountered in unary_union",
            category=RuntimeWarning,
        )
        frame = cells[["admin_uid", "spatial_level", "geometry"]].dissolve(
            by=["admin_uid", "spatial_level"], as_index=False
        )
    return frame.rename(columns={
        "admin_uid": "uid", "spatial_level": "level",
    })
