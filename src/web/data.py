"""Read mapped datasets and expose compact web-map payloads."""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import tomllib
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import mapping

from ..mapping import SpatiotemporalMappingManager


class WebDataService:
    """Adapt mapped datasets to small, browser-friendly GeoJSON responses."""

    def __init__(self, config_path: str | Path = "config/web.toml") -> None:
        path = Path(config_path).expanduser()
        if not path.is_absolute() and not path.exists():
            path = Path(__file__).resolve().parents[2] / path
        self.config_path = path.resolve()
        self.project_root = self.config_path.parent.parent
        with self.config_path.open("rb") as file:
            self.config = tomllib.load(file)
        mapping_config = self.project_root / self.config["data"]["mapping_config"]
        self.mapping = SpatiotemporalMappingManager(mapping_config)

    def bootstrap(self) -> dict[str, Any]:
        """Return UI configuration without embedding large geographic data."""

        return {
            "title": self.config["site"]["title"],
            "subtitle": self.config["site"]["subtitle"],
            "map": self.config["map"],
            "layers": self.config["layers"],
            "news": self.config.get("news", []),
        }

    @lru_cache(maxsize=1)
    def boundary(self) -> dict[str, Any]:
        """Return administrative and marine boundaries used as map context."""

        spatial = self.mapping.standard_data.load("spatial").to_crs(4326)
        spatial = spatial.assign(
            geometry=spatial.geometry.make_valid().simplify(
                0.01,
                preserve_topology=True,
            )
        )
        return _geojson(spatial, ("uid", "level", "name"))

    @lru_cache(maxsize=8)
    def layer(self, layer_id: str) -> dict[str, Any]:
        """Return one configured map layer and a compact layer summary."""

        if layer_id not in {item["id"] for item in self.config["layers"]}:
            raise KeyError(f"Unknown web layer: {layer_id}")
        if layer_id == "spatial":
            return self._spatial()
        if layer_id == "population":
            return self._population()
        if layer_id in {"generator", "storage"}:
            return self._assets(layer_id)
        if layer_id == "network":
            return self._network()
        raise KeyError(f"Web layer is not implemented: {layer_id}")

    def _spatial(self) -> dict[str, Any]:
        data = self.mapping.load("spatial").to_crs(4326)
        return _response(
            _geojson(
                data,
                ("spatial_uid", "admin_uid", "spatial_level", "area_km2"),
            ),
            count=len(data),
            value=None,
        )

    def _population(self) -> dict[str, Any]:
        data = self.mapping.load("population").to_crs(4326)
        values = pd.to_numeric(data["population"], errors="coerce").fillna(0)
        data = data.assign(population=values)
        return _response(
            _geojson(
                data,
                ("spatial_uid", "admin_uid", "spatial_level", "population"),
            ),
            count=int(values.gt(0).sum()),
            value=float(values.sum()),
        )

    def _assets(self, layer_id: str) -> dict[str, Any]:
        data = self.mapping.load(layer_id)
        value_column = (
            "capacity_mw" if layer_id == "generator" else "power_capacity_mw"
        )
        values = pd.to_numeric(data[value_column], errors="coerce").fillna(0)
        grouped = (
            data.assign(_value=values)
            .groupby(["spatial_uid", "class"], observed=True)["_value"]
            .sum()
            .unstack(fill_value=0)
        )
        cells = self.mapping.load("spatial").set_index("spatial_uid")
        cells = cells.loc[cells.index.intersection(grouped.index)].copy()
        grouped = grouped.reindex(cells.index).fillna(0)
        cells["total_mw"] = grouped.sum(axis=1)
        cells["dominant_class"] = grouped.idxmax(axis=1).astype(str)
        cells["breakdown"] = [
            {str(name): float(value) for name, value in row.items() if value > 0}
            for _, row in grouped.iterrows()
        ]
        cells["geometry"] = cells["centre_geometry"]
        cells = gpd.GeoDataFrame(cells, geometry="geometry", crs=4326).reset_index()
        return _response(
            _geojson(
                cells,
                (
                    "spatial_uid",
                    "admin_uid",
                    "spatial_level",
                    "total_mw",
                    "dominant_class",
                    "breakdown",
                ),
            ),
            count=len(data),
            value=float(values.sum()),
        )

    def _network(self) -> dict[str, Any]:
        network = self.mapping.load("network")
        threshold = float(self.config["data"]["network_min_voltage_kv"])
        branch_voltage = pd.to_numeric(network.branch["voltage_kv"])
        bus_voltage = pd.to_numeric(network.bus["voltage_kv"])
        branches = network.branch.loc[branch_voltage.ge(threshold)].to_crs(4326)
        buses = network.bus.loc[
            bus_voltage.ge(threshold) & network.bus["subclass"].eq("station_bus")
        ].to_crs(4326)
        branches = branches.assign(
            voltage_max_kv=branch_voltage.loc[branches.index],
            feature_kind="branch",
            geometry=branches.geometry.make_valid().simplify(
                0.01,
                preserve_topology=True,
            ),
        )
        buses = buses.assign(
            voltage_max_kv=bus_voltage.loc[buses.index],
            feature_kind="bus",
        )
        features = _geojson(
            branches,
            (
                "uid",
                "feature_kind",
                "class",
                "subclass",
                "current_type",
                "voltage_max_kv",
            ),
        )["features"]
        features.extend(
            _geojson(
                buses,
                ("uid", "feature_kind", "class", "subclass", "voltage_max_kv"),
            )["features"]
        )
        return _response(
            {"type": "FeatureCollection", "features": features},
            count=len(buses),
            secondary_count=len(branches),
            value=threshold,
        )


def _response(
    geojson: dict[str, Any],
    *,
    count: int,
    value: float | None,
    secondary_count: int | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {"count": count, "value": value}
    if secondary_count is not None:
        summary["secondary_count"] = secondary_count
    return {"summary": summary, "geojson": geojson}


def _geojson(
    data: gpd.GeoDataFrame,
    columns: tuple[str, ...],
) -> dict[str, Any]:
    features = []
    selected = [column for column in columns if column in data.columns]
    for row in data[[*selected, "geometry"]].itertuples(index=False, name=None):
        geometry = row[-1]
        if geometry is None or geometry.is_empty:
            continue
        features.append({
            "type": "Feature",
            "geometry": mapping(geometry),
            "properties": {
                column: _json_value(value)
                for column, value in zip(selected, row[:-1], strict=True)
            },
        })
    return {"type": "FeatureCollection", "features": features}


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or value is pd.NA:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _max_voltage(value: Any) -> float:
    if value is None or value is pd.NA:
        return 0.0
    if isinstance(value, str):
        values = value.split(",")
    else:
        try:
            values = list(value)
        except TypeError:
            values = [value]
    numeric = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
    return float(numeric.max()) if not numeric.empty else 0.0


def encode_json(payload: dict[str, Any]) -> bytes:
    """Encode a response consistently for the HTTP layer."""

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
