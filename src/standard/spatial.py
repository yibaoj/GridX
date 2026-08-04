"""Multi-source spatial-unit standardization."""

from __future__ import annotations

import json
from pathlib import Path
import warnings

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape

from .base import _Standardizer
from .geometry import polygonal_geometry
from .schema import _finalize_frame, _write_geodataframe


class _SpatialStandardizer(_Standardizer):
    def build(self) -> gpd.GeoDataFrame:
        frames = [
            self._read_source(source_id)
            for source_id in self.config["source_ids"]
        ]
        frame = pd.concat(frames, ignore_index=True, sort=False)
        result = _finalize_frame(
            frame,
            schema_id="spatial",
            string_columns=tuple(
                column
                for column in frame.columns
                if column not in {"geometry"}
                and column not in {
                    "uid", "level", "geometry_method", "observed_at",
                    "valid_from", "valid_to", "source_id", "source_uid",
                }
            ),
        )
        _write_geodataframe(result, self.output())
        return result

    def _read_source(self, source_id: str) -> gpd.GeoDataFrame:
        metadata = self.manager.raw_data.catalog.loc[source_id]
        try:
            options = json.loads(metadata.get("options_json") or "{}")
        except json.JSONDecodeError as error:
            raise ValueError(f"{source_id} has invalid options_json.") from error
        level = str(options.get("spatial_level", "")).strip()
        level_field = str(options.get("spatial_level_field", "")).strip()
        if not level and not level_field:
            raise ValueError(
                f"Spatial source {source_id!r} must define "
                "options_json.spatial_level or spatial_level_field."
            )
        reader = str(options.get("reader", metadata["file_format"]))
        if reader == "geojson":
            source = _read_geojson(self.source(source_id))
        elif reader == "vector_archive":
            source = _read_vector_archive(self.source(source_id), options)
        else:
            raise ValueError(f"Unsupported spatial reader {reader!r} for {source_id}.")
        source = source.to_crs("EPSG:4326")
        source_uid_field = str(options.get("source_uid_field", "")).strip()
        if source_uid_field not in source:
            raise KeyError(
                f"Spatial source {source_id!r} has no UID field "
                f"{source_uid_field!r}."
            )
        if level_field and level_field not in source:
            raise KeyError(
                f"Spatial source {source_id!r} has no level field "
                f"{level_field!r}."
            )
        if bool(options.get("dissolve", False)):
            source = _dissolve_source(source, source_uid_field)
        source_uids = source[source_uid_field].astype("string")
        if source_uids.isna().any() or source_uids.duplicated().any():
            raise ValueError(
                f"Spatial source {source_id!r} does not have unique "
                f"{source_uid_field!r} values after configured processing."
            )

        records = []
        reserved = {
            "uid", "level", "geometry", "geometry_method", "observed_at",
            "valid_from", "valid_to", "source_id", "source_uid",
        }
        for index, row in source.iterrows():
            geometry = polygonal_geometry(row.geometry)
            if geometry.is_empty:
                continue
            source_uid = str(row[source_uid_field])
            level_value = row[level_field] if level_field else level
            row_level = "" if pd.isna(level_value) else str(level_value).strip()
            if not row_level:
                raise ValueError(
                    f"Spatial source {source_id!r} has an empty level for "
                    f"source UID {source_uid!r}."
                )
            record = {
                key: _json_value(value)
                for key, value in row.drop(labels="geometry").items()
                if key not in reserved
            }
            record.update({
                "uid": f"{source_id}:{source_uid}",
                "level": row_level,
                "geometry": geometry,
                "geometry_method": (
                    "source_geometry_dissolved"
                    if bool(options.get("dissolve", False))
                    else "source_geometry_repaired"
                ),
                "observed_at": options.get("observed_at", pd.NA),
                "valid_from": options.get("valid_from", pd.NA),
                "valid_to": options.get("valid_to", pd.NA),
                "source_id": source_id,
                "source_uid": source_uid,
                "source_version": metadata.get("version") or pd.NA,
            })
            records.append(record)
        return gpd.GeoDataFrame(records, geometry="geometry", crs=source.crs)


def _read_geojson(path: Path) -> gpd.GeoDataFrame:
    content = json.loads(path.read_text())
    records = []
    for feature in content["features"]:
        record = dict(feature.get("properties") or {})
        record["geometry"] = shape(feature["geometry"])
        records.append(record)
    return gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")


def _read_vector_archive(
    path: Path,
    options: dict[str, object],
) -> gpd.GeoDataFrame:
    configured_layers = options.get("layers")
    if not isinstance(configured_layers, dict) or not configured_layers:
        raise ValueError("vector_archive spatial sources require a layers mapping.")
    uri = f"zip://{path}"
    available = set(gpd.list_layers(uri)["name"])
    missing = set(configured_layers).difference(available)
    if missing:
        raise ValueError(f"Configured archive layers are missing: {sorted(missing)}")
    frames = []
    for layer, zone_class in configured_layers.items():
        frame = gpd.read_file(uri, layer=layer)
        frame["zone_class"] = str(zone_class)
        frames.append(frame)
    result = gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True, sort=False),
        geometry="geometry",
        crs=frames[0].crs,
    )
    for column, accepted in dict(options.get("filters", {})).items():
        if column not in result:
            raise KeyError(f"Spatial source has no configured filter field {column!r}.")
        values = accepted if isinstance(accepted, list) else [accepted]
        result = result.loc[result[column].isin(values)].copy()
    keep_fields = list(options.get("keep_fields", []))
    required = {
        str(options.get("source_uid_field", "")), "zone_class", "geometry"
    }
    missing = required.difference(result.columns)
    if missing:
        raise KeyError(f"Spatial source is missing fields: {sorted(missing)}")
    return result.loc[:, [
        column
        for column in [*keep_fields, "zone_class", "geometry"]
        if column in result
    ]]


def _dissolve_source(
    source: gpd.GeoDataFrame,
    source_uid_field: str,
) -> gpd.GeoDataFrame:
    records = []
    for source_uid, group in source.groupby(source_uid_field, sort=True):
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="invalid value encountered in unary_union",
                category=RuntimeWarning,
            )
            geometry = polygonal_geometry(
                gpd.GeoSeries(
                    group.geometry.map(polygonal_geometry), crs=source.crs
                ).union_all()
            )
        record = {source_uid_field: source_uid, "geometry": geometry}
        for column in group.columns.difference([source_uid_field, "geometry"]):
            values = group[column].dropna().astype(str).unique()
            record[column] = ",".join(sorted(values)) if len(values) else pd.NA
        records.append(record)
    return gpd.GeoDataFrame(records, geometry="geometry", crs=source.crs)


def _json_value(value: object) -> object:
    return (
        json.dumps(value, ensure_ascii=False)
        if isinstance(value, (dict, list))
        else value
    )
