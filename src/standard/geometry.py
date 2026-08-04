"""Geometry helpers shared by standardization and downstream layers."""

from __future__ import annotations

import warnings

from shapely import make_valid
from shapely.geometry import GeometryCollection
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

__all__ = ["polygonal_geometry"]


def polygonal_geometry(geometry: BaseGeometry) -> BaseGeometry:
    """Extract valid polygonal parts without simplifying coordinates."""

    if geometry is None or geometry.is_empty:
        return GeometryCollection()
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="invalid value encountered in make_valid",
            category=RuntimeWarning,
        )
        geometry = make_valid(geometry)
    if geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return geometry
    if not hasattr(geometry, "geoms"):
        return GeometryCollection()
    parts = [polygonal_geometry(part) for part in geometry.geoms]
    return unary_union([part for part in parts if not part.is_empty])
