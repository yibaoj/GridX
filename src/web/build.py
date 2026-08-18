"""Build compact web payloads from the same prepared data used by backend plots."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import tomllib
from typing import Any
import warnings

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import mapping
import xarray as xr

from ..app import UnitCommitmentApplication
from ..app.uc.plot import prepare_dispatch_data
from ..case import PowerSystemCaseManager
from ..case.plot import load_with_bus_geometry
from ..mapping import SpatiotemporalMappingManager
from ..mapping.plot import prepare_asset_pies
from ..standard import StandardNetwork, StandardDataManager
from ..standard.plot import (
    CATEGORY_COLORS,
    capacity_legend_values,
    prepare_asset_points,
    prepare_network_plot,
    prepare_population_plot,
    prepare_timeseries_plot,
    network_style_sort_key,
)
from ..visualization.labels import class_label


class WebDataBuilder:
    """Materialize browser-ready data without duplicating plot calculations."""

    def __init__(self, config_path: str | Path = "config/web.toml") -> None:
        path = Path(config_path).expanduser()
        if not path.is_absolute() and not path.exists():
            path = Path(__file__).resolve().parents[2] / path
        self.config_path = path.resolve()
        self.project_root = self.config_path.parent.parent
        with self.config_path.open("rb") as file:
            self.config = tomllib.load(file)
        options = self.config["data"]
        self.output_root = self.project_root / options["output_root"]
        self.year = int(options["plot_year"])
        self.simplify = float(options["geometry_simplify_degrees"])
        self.standard = StandardDataManager(
            self.project_root / options["standard_config"]
        )
        self.mapping = SpatiotemporalMappingManager(
            self.project_root / options["mapping_config"], self.standard
        )
        self.case = PowerSystemCaseManager(
            self.project_root / options["case_config"],
            mapped_data=self.mapping,
        )
        self.uc_config = self.project_root / options["uc_config"]
        self._case_cache = None

    def build(self) -> dict[str, Any]:
        """Build boundaries, map layers, and saved application series."""

        self.output_root.mkdir(parents=True, exist_ok=True)
        self._remove_legacy_duplicates()
        boundary = self._boundary()
        self._write("boundary/common.json.gz", boundary)
        for stage in ("standard", "mapping", "case"):
            self._build_stage(stage)
        self._write("application/uc.json.gz", self._application())
        manifest = {
            "year": self.year,
            "stages": ["standard", "mapping", "case", "application"],
            "layers": [item["id"] for item in self.config["layers"]],
            "resource_classes": self._resource_classes(),
        }
        self._write("manifest.json.gz", manifest)
        return manifest

    def _remove_legacy_duplicates(self) -> None:
        """Remove payloads superseded by shared-path aliases."""

        for stage in ("standard", "mapping", "case"):
            (self.output_root / f"boundary/{stage}.json.gz").unlink(
                missing_ok=True
            )
        for item in self._resource_classes():
            (self.output_root / f"layers/case/resource/{item['id']}.json.gz").unlink(
                missing_ok=True
            )

    def _build_stage(self, stage: str) -> None:
        for dataset_id in ("generator", "storage"):
            self._write(
                f"layers/{stage}/{dataset_id}.json.gz",
                self._assets(stage, dataset_id),
            )
        self._write(
            f"layers/{stage}/population.json.gz", self._population(stage)
        )
        self._write(f"layers/{stage}/load.json.gz", self._timeseries(stage, "load"))
        resource_classes = [item["id"] for item in self._resource_classes()]
        for class_name in resource_classes:
            relative = f"layers/{stage}/resource/{class_name}.json.gz"
            if stage == "case":
                continue
            else:
                self._write(
                    relative,
                    self._timeseries(stage, "resource", class_name=class_name),
                )
        self._write(f"layers/{stage}/network.json.gz", self._network(stage))

    def _dataset(self, stage: str, dataset_id: str) -> object:
        if stage == "standard":
            return self.standard.load(dataset_id)
        if stage == "mapping":
            return self.mapping.load(dataset_id)
        return getattr(self._case_data(), dataset_id)

    def _case_data(self):
        if self._case_cache is None:
            self._case_cache = self.case.load()
        return self._case_cache

    def _boundary(self) -> dict[str, Any]:
        data = self.standard.load("spatial").to_crs(4326)
        data = data.assign(
            geometry=_simplify_geometry(data.geometry, self.simplify),
            spatial_uid=data["uid"].astype(str),
            spatial_level=data["level"].astype(str),
        )
        return _geojson(data, ("spatial_uid", "spatial_level", "name"))

    def _assets(self, stage: str, dataset_id: str) -> dict[str, Any]:
        source = self._dataset(stage, dataset_id)
        data = source.data if hasattr(source, "data") else source
        capacity_column = (
            "capacity_mw" if dataset_id == "generator" else "power_capacity_mw"
        )
        if stage == "standard":
            frame, reference, classes = prepare_asset_points(data, capacity_column)
            frame = frame.assign(
                location_uid=frame["uid"].astype(str),
                total_mw=frame["_capacity"],
                breakdown=[
                    {str(item): float(value)}
                    for item, value in zip(
                        frame["_class"], frame["_capacity"], strict=True
                    )
                ],
            )
            display_mode = "point"
        else:
            frame = data.copy()
            if stage == "case":
                frame["spatial_uid"] = frame["bus_uid"].astype(str)
                cells = self._case_data().network.bus.data[["uid", "geometry"]].copy()
                cells = cells.rename(columns={"uid": "spatial_uid"})
            else:
                cells = self.mapping.load("spatial")
            frame, reference, classes = prepare_asset_pies(
                frame, cells, capacity_column
            )
            frame = frame.rename(columns={
                "spatial_uid": "location_uid",
                "_total": "total_mw",
                "_breakdown": "breakdown",
            })
            display_mode = "pie"
        frame = frame.to_crs(4326)
        return _response(
            _geojson(frame, ("location_uid", "total_mw", "breakdown")),
            count=len(data), value=float(pd.to_numeric(
                data[capacity_column], errors="coerce"
            ).sum()),
            display_mode=display_mode,
            categories=_category_metadata(classes),
            capacity_reference_mw=reference,
            capacity_legend_mw=capacity_legend_values(reference),
            unit="MW",
        )

    def _population(self, stage: str) -> dict[str, Any]:
        frame = prepare_population_plot(self._dataset(stage, "population"))
        frame = frame.to_crs(4326)
        return _response(
            _geojson(frame, (
                "uid", "spatial_uid", "spatial_level", "_value", "_raw_value"
            ), property_names={"_value": "value", "_raw_value": "raw_value"}),
            count=len(frame), value=float(frame["_raw_value"].sum()),
            title="人口", unit="log10(人口 + 1)", value_transform="log10(x + 1)",
        )

    def _timeseries(
        self,
        stage: str,
        dataset_id: str,
        *,
        class_name: str | None = None,
    ) -> dict[str, Any]:
        data = self._dataset(stage, dataset_id)
        if stage == "case" and dataset_id == "load":
            data = load_with_bus_geometry(self._case_data())
        variable = "demand_mw" if dataset_id == "load" else "availability_pu"
        quantity = "load" if dataset_id == "load" else "resource"
        selected = class_name
        if selected is None:
            selected = str(data["class"].values.astype(str)[0])
        prepared = prepare_timeseries_plot(
            data, variable=variable, year=self.year,
            class_name=selected, quantity=quantity, language="zh",
        )
        frame, metadata = prepared[selected]
        frame = frame.assign(location_uid=data["uid"].values.astype(str)).to_crs(4326)
        values = pd.to_numeric(frame["_value"], errors="coerce")
        if isinstance(data, xr.Dataset) and stage != "case":
            data.close()
        return _response(
            _geojson(
                frame, ("location_uid", "_value"),
                property_names={"_value": "value"},
            ),
            count=int(values.notna().sum()), value=float(values.mean()),
            selected_class=selected, title=metadata["title"],
            unit=metadata["unit"], year=self.year,
            vmin=metadata["vmin"], vmax=metadata["vmax"],
        )

    def _network(self, stage: str) -> dict[str, Any]:
        source = self._dataset(stage, "network")
        network = StandardNetwork(
            source.bus.data if hasattr(source.bus, "data") else source.bus,
            source.branch.data if hasattr(source.branch, "data") else source.branch,
            source.transformer.data if hasattr(source.transformer, "data") else source.transformer,
            source.converter.data if hasattr(source.converter, "data") else source.converter,
        )
        branches, buses, style_colors = prepare_network_plot(network)
        branches = branches.to_crs(4326)
        branches = branches.assign(
            geometry=_simplify_geometry(branches.geometry, self.simplify),
            feature_kind="branch",
        )
        buses = buses.to_crs(4326).assign(feature_kind="bus")
        features = _geojson(branches, (
            "uid", "feature_kind", "_style", "_voltage_kv", "_current_type"
        ), property_names={
            "_style": "style", "_voltage_kv": "voltage_kv",
            "_current_type": "current_type",
        })["features"]
        features.extend(_geojson(buses, (
            "uid", "feature_kind", "_node_type", "voltage_kv", "current_type"
        ), property_names={"_node_type": "node_type"})["features"])
        legend = [{
            "label": str(style), "color": _color_hex(style_colors[str(style)]),
            "dash": str(style).endswith(" DC"),
        } for style in sorted(style_colors, key=network_style_sort_key)]
        return _response(
            {"type": "FeatureCollection", "features": features},
            count=len(buses), value=None, secondary_count=len(branches),
            branch_legend=legend,
            node_legend=[
                {"id": "junction", "label": "Junction", "color": "#596267"},
                {"id": "station", "label": "Station", "color": "#d1495b"},
            ],
        )

    def _application(self) -> dict[str, Any]:
        result = UnitCommitmentApplication(self._case_data(), self.uc_config).load()
        data = result.data
        prepared = prepare_dispatch_data(result)
        generation = prepared["generation"]
        discharge = prepared["storage_discharge"]
        charging = prepared["storage_charge"]
        load = prepared["load"]
        series = [
            *[_series(str(name), "generation", values) for name, values in generation.items() if values.abs().sum() > 0],
            *[_series(str(name), "storage_discharge", values) for name, values in discharge.items() if values.abs().sum() > 0],
            *[_series(str(name), "storage_charge", values) for name, values in charging.items() if values.abs().sum() > 0],
            _series("load", "load", load),
        ]
        payload = {
            "time": [pd.Timestamp(value).isoformat() for value in data.time.values],
            "series": series,
            "categories": _category_metadata([item["id"] for item in series]),
            "summary": {str(key): _json_value(value) for key, value in result.summary.items()},
        }
        data.close()
        return payload

    def _resource_classes(self) -> list[dict[str, str]]:
        data = self.mapping.load("resource")
        classes = data["class"].values.astype(str).tolist()
        data.close()
        return [
            {"id": item, "label": class_label(item, "zh")} for item in classes
        ]

    def _write(self, relative: str, payload: dict[str, Any]) -> None:
        path = self.output_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"),
        ).encode()
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        with gzip.open(temporary, "wb", compresslevel=6) as file:
            file.write(encoded)
        temporary.replace(path)


def _category_metadata(classes: list[str]) -> list[dict[str, str]]:
    return [{
        "id": str(item), "label": class_label(item, "zh"),
        "color": _color_hex(CATEGORY_COLORS.get(str(item), "#909691")),
    } for item in dict.fromkeys(classes)]


def _simplify_geometry(geometry: gpd.GeoSeries, tolerance: float) -> gpd.GeoSeries:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="invalid value encountered in simplify", category=RuntimeWarning
        )
        return geometry.make_valid().simplify(
            tolerance, preserve_topology=True
        )


def _color_hex(value: object) -> str:
    if isinstance(value, str):
        return value
    red, green, blue = np.asarray(value)[:3]
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


def _series(name: str, kind: str, values: pd.Series) -> dict[str, Any]:
    return {"id": name, "kind": kind, "values": [float(value) for value in values]}


def _response(
    geojson: dict[str, Any], *, count: int, value: float | None,
    secondary_count: int | None = None, **metadata: object,
) -> dict[str, Any]:
    summary: dict[str, Any] = {"count": count, "value": value, **metadata}
    if secondary_count is not None:
        summary["secondary_count"] = secondary_count
    return {"summary": summary, "geojson": geojson}


def _geojson(
    data: gpd.GeoDataFrame,
    columns: tuple[str, ...],
    *,
    property_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    names = property_names or {}
    selected = [column for column in columns if column in data.columns]
    features = []
    for row in data[[*selected, "geometry"]].itertuples(index=False, name=None):
        geometry = row[-1]
        if geometry is None or geometry.is_empty:
            continue
        features.append({
            "type": "Feature",
            "geometry": mapping(geometry),
            "properties": {
                names.get(column, column): _json_value(value)
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/web.toml")
    options = parser.parse_args()
    manifest = WebDataBuilder(options.config).build()
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
