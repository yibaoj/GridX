"""Node classification, deduplication, and validation for OSM networks."""

from __future__ import annotations

from collections import Counter, defaultdict

import geopandas as gpd
import pandas as pd

from .schema import _numeric, _partial_time


def station_rows(
    stations: gpd.GeoDataFrame,
    source_id: str,
    observed_at: object,
    options: dict[str, object],
) -> list[dict[str, object]]:
    """Create station nodes only from explicit OSM facility features."""

    rows = []
    threshold = float(options["transmission_voltage_threshold_kv"])
    for station in stations.itertuples():
        subclass, method = _station_subclass(station, threshold)
        rows.append({
            "uid": station.source_uid,
            "class": "station",
            "subclass": subclass,
            "status": station.standard_status,
            "voltage_kv": station.voltage_values,
            "voltage_raw": station.voltage,
            "voltage_assignment_method": (
                "source_tag" if station.voltage_values else "unresolved"
            ),
            "voltage_inferred": False,
            "voltage_reference_uid": pd.NA,
            "geometry": station.geometry,
            "geometry_method": "source_geometry",
            "observed_at": observed_at,
            "valid_from": _partial_time(
                station.start_date if hasattr(station, "start_date") else pd.NA
            ),
            "valid_to": _partial_time(
                station.closing_date if hasattr(station, "closing_date") else pd.NA
            ),
            "source_id": source_id,
            "source_uid": station.source_uid.removeprefix("osm:"),
            "name": station.name,
            "operator": station.operator,
            "frequency_hz": _numeric(station.frequency),
            "substation_raw": station.substation,
            "node_classification_method": method,
            "merged_source_uids": pd.NA,
        })
    return rows


def transformer_rows(
    transformers: gpd.GeoDataFrame,
    source_id: str,
    observed_at: object,
) -> list[dict[str, object]]:
    """Create station nodes from OSM transformers used by retained branches."""

    return [{
        "uid": transformer.source_uid,
        "class": "station",
        "subclass": "transformer",
        "status": transformer.standard_status,
        "voltage_kv": transformer.voltage_values,
        "voltage_raw": transformer.voltage,
        "voltage_assignment_method": (
            "source_tag" if transformer.voltage_values else "unresolved"
        ),
        "voltage_inferred": False,
        "voltage_reference_uid": pd.NA,
        "geometry": transformer.geometry,
        "geometry_method": "source_geometry",
        "observed_at": observed_at,
        "valid_from": _partial_time(
            transformer.start_date
            if hasattr(transformer, "start_date") else pd.NA
        ),
        "valid_to": _partial_time(
            transformer.closing_date
            if hasattr(transformer, "closing_date") else pd.NA
        ),
        "source_id": source_id,
        "source_uid": transformer.source_uid.removeprefix("osm:"),
        "name": transformer.name,
        "operator": transformer.operator,
        "frequency_hz": _numeric(transformer.frequency),
        "substation_raw": pd.NA,
        "node_classification_method": "source_tag",
        "merged_source_uids": pd.NA,
    } for transformer in transformers.itertuples()]


def voltage_station_subclass(voltages: object, threshold_kv: float) -> str:
    values = list(voltages or [])
    return "transmission" if values and max(values) >= threshold_kv else "distribution"


def fill_missing_node_voltages(
    rows: list[dict[str, object]],
    options: dict[str, object],
) -> None:
    """Fill missing facility voltages from the nearest known node, with provenance."""

    frame = gpd.GeoDataFrame(
        {
            "uid": [row["uid"] for row in rows],
            "voltage_kv": [row.get("voltage_kv") or [] for row in rows],
        },
        geometry=[row["geometry"] for row in rows],
        crs="EPSG:4326",
    )
    known = frame["voltage_kv"].map(bool)
    if known.any() and (~known).any():
        points = frame.copy()
        points.geometry = points.geometry.map(
            lambda geometry: geometry.representative_point()
            if geometry is not None and not geometry.is_empty else None
        )
        points = points.to_crs(options["metric_crs"])
        matches = gpd.sjoin_nearest(
            points.loc[~known, ["uid", "geometry"]],
            points.loc[known, ["uid", "voltage_kv", "geometry"]],
            how="left",
            distance_col="voltage_reference_distance_m",
            lsuffix="target",
            rsuffix="reference",
        ).sort_values(
            ["uid_target", "voltage_reference_distance_m", "uid_reference"]
        ).drop_duplicates("uid_target")
        by_uid = {row["uid"]: row for row in rows}
        for match in matches.itertuples():
            if pd.isna(match.uid_reference):
                continue
            target = by_uid[match.uid_target]
            target["voltage_kv"] = list(by_uid[match.uid_reference]["voltage_kv"])
            target["voltage_assignment_method"] = "inferred_nearest_node"
            target["voltage_inferred"] = True
            target["voltage_reference_uid"] = match.uid_reference

    known_values = [
        voltage for row in rows for voltage in (row.get("voltage_kv") or [])
    ]
    fallback = (
        Counter(known_values).most_common(1)[0][0]
        if known_values else float(options.get("fallback_voltage_kv", 220.0))
    )
    for row in rows:
        if row.get("voltage_kv"):
            continue
        row["voltage_kv"] = [fallback]
        row["voltage_assignment_method"] = "inferred_global_mode"
        row["voltage_inferred"] = True
        row["voltage_reference_uid"] = pd.NA


def deduplicate_node_rows(
    rows: list[dict[str, object]],
    branches: gpd.GeoDataFrame,
) -> list[dict[str, object]]:
    """Merge only nodes with identical geometry and core electrical attributes."""

    groups: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        geometry = row.get("geometry")
        geometry_key = (
            geometry.wkb_hex
            if geometry is not None and not geometry.is_empty
            else f"missing:{row['uid']}"
        )
        groups[(
            geometry_key,
            row.get("class"),
            row.get("subclass"),
            _scalar(row.get("status")),
            tuple(sorted(row.get("voltage_kv") or [])),
        )].append(row)

    connected = set(branches["from_uid"]) | set(branches["to_uid"])
    replacements: dict[str, str] = {}
    result = []
    for group in groups.values():
        representative = min(
            group,
            key=lambda row: (str(row["uid"]) not in connected, str(row["uid"])),
        )
        result.append(representative)
        if len(group) == 1:
            continue
        merged = set()
        for row in group:
            values = row.get("merged_source_uids")
            if not pd.isna(values):
                merged.update(str(values).split(";"))
            merged.add(str(row["uid"]))
            if row is not representative:
                replacements[str(row["uid"])] = str(representative["uid"])
        representative["merged_source_uids"] = ";".join(sorted(merged))

    if replacements:
        for column in ("from_uid", "to_uid"):
            branches[column] = branches[column].map(
                lambda uid: replacements.get(str(uid), uid)
            ).astype("string")
        loops = branches["from_uid"].eq(branches["to_uid"])
        branches.drop(index=branches.index[loops], inplace=True)
        branches.reset_index(drop=True, inplace=True)
    return result


def validate_nodes(
    nodes: gpd.GeoDataFrame,
    branches: gpd.GeoDataFrame,
) -> None:
    """Validate node semantics, endpoint integrity, voltage, and node degree."""

    if not branches["voltage_kv"].map(len).eq(1).all():
        raise ValueError("Every standardized network branch must have one voltage.")
    if not nodes["voltage_kv"].map(len).ge(1).all():
        raise ValueError("Every standardized network node must have a voltage.")
    missing = (set(branches["from_uid"]) | set(branches["to_uid"])).difference(
        nodes["uid"]
    )
    if missing:
        raise ValueError(f"Network branches reference missing nodes: {len(missing)}")

    allowed = {
        "station": {"transmission", "distribution", "converter", "transformer"},
        "junction": {"same_voltage", "line_terminal"},
    }
    if nodes[["class", "subclass"]].isna().any().any():
        raise ValueError("Every network node must have class and subclass.")
    invalid = nodes.apply(
        lambda row: row["class"] not in allowed
        or row["subclass"] not in allowed[row["class"]],
        axis=1,
    )
    if invalid.any():
        raise ValueError(f"Network contains {int(invalid.sum())} invalid node classes.")
    station_methods = set(nodes.loc[
        nodes["class"].eq("station"), "node_classification_method"
    ].dropna())
    if not station_methods <= {"source_tag", "inferred_voltage_threshold"}:
        raise ValueError("A station node was not derived from an OSM facility feature.")

    branch_voltage = branches["voltage_kv"].map(lambda values: values[0])
    endpoints = pd.concat([
        pd.DataFrame({"uid": branches["from_uid"], "voltage": branch_voltage}),
        pd.DataFrame({"uid": branches["to_uid"], "voltage": branch_voltage}),
    ], ignore_index=True)
    junctions = set(nodes.loc[nodes["class"].eq("junction"), "uid"])
    cross_voltage = endpoints.loc[endpoints["uid"].isin(junctions)].groupby(
        "uid"
    )["voltage"].nunique().gt(1)
    if cross_voltage.any():
        raise ValueError(
            f"Network contains {int(cross_voltage.sum())} cross-voltage junctions."
        )

    degree = endpoints["uid"].value_counts()
    terminal_degree = nodes.loc[
        nodes["subclass"].eq("line_terminal"), "uid"
    ].map(degree).fillna(0)
    junction_degree = nodes.loc[
        nodes["subclass"].eq("same_voltage"), "uid"
    ].map(degree).fillna(0)
    if not terminal_degree.eq(1).all():
        raise ValueError("Every line_terminal junction must have degree one.")
    if not junction_degree.ge(2).all():
        raise ValueError("Every same_voltage junction must have degree at least two.")


def _station_subclass(station: object, threshold_kv: float) -> tuple[str, str]:
    raw = "" if pd.isna(station.substation) else str(station.substation).lower()
    if station.standard_type == "converter" or "converter" in raw:
        return "converter", "source_tag"
    if raw == "transmission":
        return "transmission", "source_tag"
    if raw in {"distribution", "minor_distribution"}:
        return "distribution", "source_tag"
    return (
        voltage_station_subclass(station.voltage_values, threshold_kv),
        "inferred_voltage_threshold",
    )


def _scalar(value: object) -> object:
    return None if pd.isna(value) else value
