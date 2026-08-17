"""Voltage, geographic-node, and electrical-network modeling helpers."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
import re

import geopandas as gpd
import pandas as pd

from .schema import (
    REQUIRED_COLUMNS,
    _finalize_frame,
    _numeric,
    _partial_time,
)
from .model import StandardNetwork


def tag_numbers(value: object) -> list[float]:
    """Parse a semicolon-separated numeric OSM tag."""

    if pd.isna(value):
        return []
    numbers = []
    for token in str(value).split(";"):
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", token)
        if match is None:
            return []
        numbers.append(float(match.group(1)))
    return numbers


def current_type(frequency: object, name: object) -> str:
    """Infer AC/DC from OSM frequency and line name tags."""

    frequencies = re.split(r"[;,/]", "" if pd.isna(frequency) else str(frequency))
    if any(value.strip() in {"0", "0.0"} for value in frequencies):
        return "DC"
    if re.search(r"直流|HVDC", "" if pd.isna(name) else str(name), re.I):
        return "DC"
    return "AC"


def voltage_label(voltage: float) -> str:
    return f"{voltage:g}"


def resolve_line_voltages(
    line_records: dict[str, object],
    way_references: dict[str, list[int]],
    options: dict[str, object],
) -> dict[str, dict[str, object]]:
    """Fill missing line voltages while recording each inference method."""

    resolved: dict[str, dict[str, object]] = {}
    lines_by_node: dict[int, list[str]] = defaultdict(list)
    for line_uid in sorted(line_records):
        line = line_records[line_uid]
        raw_sequence = [
            value / 1000 for value in tag_numbers(line.voltage) if value >= 1000
        ]
        parsed = [float(value) for value in line.voltage_values]
        if raw_sequence and set(raw_sequence) == set(parsed):
            voltages, method = list(dict.fromkeys(raw_sequence)), "source_tag"
        elif parsed:
            voltages, method = parsed, "source_tag_normalized"
        else:
            voltages, method = [], "unresolved"
        resolved[line_uid] = {
            "voltage_values": voltages,
            "voltage_method": method,
            "voltage_inferred": False,
            "voltage_reference_uid": pd.NA,
        }
        for node_id in way_references[line_uid]:
            lines_by_node[node_id].append(line_uid)

    neighbours: dict[str, set[str]] = defaultdict(set)
    for line_uids in lines_by_node.values():
        ordered = sorted(set(line_uids))
        for line_uid in ordered:
            neighbours[line_uid].update(
                other for other in ordered if other != line_uid
            )

    queue = deque(
        line_uid for line_uid in sorted(resolved)
        if resolved[line_uid]["voltage_values"]
    )
    source_reference = {line_uid: line_uid for line_uid in queue}
    while queue:
        line_uid = queue.popleft()
        for neighbour in sorted(neighbours[line_uid]):
            if resolved[neighbour]["voltage_values"]:
                continue
            reference = source_reference[line_uid]
            resolved[neighbour].update({
                "voltage_values": list(resolved[reference]["voltage_values"]),
                "voltage_method": "inferred_nearest_connected_line",
                "voltage_inferred": True,
                "voltage_reference_uid": reference,
            })
            source_reference[neighbour] = reference
            queue.append(neighbour)

    unresolved = [
        uid for uid, values in resolved.items() if not values["voltage_values"]
    ]
    known_uids = [
        uid for uid, values in resolved.items() if values["voltage_values"]
    ]
    if unresolved and known_uids:
        geometry = gpd.GeoDataFrame(
            {
                "uid": list(line_records),
                "geometry": [line_records[uid].geometry for uid in line_records],
            },
            crs="EPSG:4326",
        )
        geometry = geometry.loc[
            geometry.geometry.notna() & ~geometry.geometry.is_empty
        ].to_crs(options["metric_crs"])
        matches = gpd.sjoin_nearest(
            geometry.loc[geometry["uid"].isin(unresolved)],
            geometry.loc[geometry["uid"].isin(known_uids)],
            how="left",
            distance_col="voltage_reference_distance_m",
            lsuffix="target",
            rsuffix="reference",
        ).sort_values(
            ["uid_target", "voltage_reference_distance_m", "uid_reference"]
        ).drop_duplicates("uid_target")
        for match in matches.itertuples():
            if pd.isna(match.uid_reference):
                continue
            resolved[match.uid_target].update({
                "voltage_values": list(
                    resolved[match.uid_reference]["voltage_values"]
                ),
                "voltage_method": "inferred_nearest_line_geometry",
                "voltage_inferred": True,
                "voltage_reference_uid": match.uid_reference,
            })

    known = [
        voltage
        for values in resolved.values()
        for voltage in values["voltage_values"]
    ]
    fallback = (
        Counter(known).most_common(1)[0][0]
        if known else float(options.get("fallback_voltage_kv", 220.0))
    )
    for values in resolved.values():
        if values["voltage_values"]:
            continue
        values.update({
            "voltage_values": [fallback],
            "voltage_method": "inferred_global_mode",
            "voltage_inferred": True,
            "voltage_reference_uid": pd.NA,
        })
    return resolved


def line_systems(
    line: object,
    voltage: dict[str, object],
    options: dict[str, object],
) -> list[dict[str, object]]:
    """Split one physical OSM way into auditable single-voltage systems."""

    voltage_values = list(voltage["voltage_values"])
    raw_sequence = [
        value / 1000 for value in tag_numbers(line.voltage) if value >= 1000
    ]
    if set(raw_sequence) != set(voltage_values):
        raw_sequence = []
    voltage_order = list(dict.fromkeys(raw_sequence)) or voltage_values
    circuit_tokens = tag_numbers(line.circuits)
    cable_tokens = tag_numbers(line.cables)
    ac_dc = current_type(line.frequency, line.name)
    cables_per_circuit = float(options.get(
        "ac_cables_per_circuit" if ac_dc == "AC" else "dc_cables_per_circuit",
        3 if ac_dc == "AC" else 2,
    ))

    def positional(values: list[float]) -> dict[float, float] | None:
        if not raw_sequence or len(values) <= 1:
            return None
        if len(values) == len(raw_sequence):
            result: dict[float, float] = defaultdict(float)
            for level, value in zip(raw_sequence, values, strict=True):
                result[level] += value
            return dict(result)
        if len(values) == len(voltage_order):
            return dict(zip(voltage_order, values, strict=True))
        return None

    circuit_map = positional(circuit_tokens)
    cable_map = positional(cable_tokens)
    if len(voltage_order) == 1:
        level = voltage_order[0]
        if circuit_tokens:
            circuit_map, circuit_method = {level: sum(circuit_tokens)}, "source_total"
        elif cable_tokens:
            circuit_map = {level: sum(cable_tokens) / cables_per_circuit}
            circuit_method = "inferred_from_cables"
        else:
            circuit_map = {level: float(options.get("default_circuits", 1.0))}
            circuit_method = "inferred_default_per_voltage"
        if cable_tokens:
            cable_map, cable_method = {level: sum(cable_tokens)}, "source_total"
        else:
            cable_map = {level: circuit_map[level] * cables_per_circuit}
            cable_method = "inferred_from_circuits"
    else:
        if circuit_map is not None:
            circuit_method = "position_aligned"
        elif (
            len(circuit_tokens) == 1
            and raw_sequence
            and circuit_tokens[0] == len(raw_sequence)
        ):
            circuit_map = {
                level: float(count) for level, count in Counter(raw_sequence).items()
            }
            circuit_method = "voltage_sequence"
        elif not circuit_tokens and cable_map:
            circuit_map = {
                level: cables / cables_per_circuit
                for level, cables in cable_map.items()
            }
            circuit_method = "inferred_from_cables"
        elif circuit_tokens:
            circuit_map = _balanced_allocation(sum(circuit_tokens), voltage_order)
            circuit_method = "inferred_balanced_front_remainder"
        else:
            circuit_map = {
                level: float(options.get("default_circuits", 1.0))
                for level in voltage_order
            }
            circuit_method = "inferred_default_per_voltage"

        if cable_map is not None:
            cable_method = "position_aligned"
        elif cable_tokens:
            cable_map = _balanced_allocation(sum(cable_tokens), voltage_order)
            cable_method = "inferred_balanced_front_remainder"
        else:
            cable_map = {
                level: circuits * cables_per_circuit
                for level, circuits in circuit_map.items()
            }
            cable_method = "inferred_from_circuits"

    direct = {"source_total", "position_aligned", "voltage_sequence"}
    return [{
        "voltage_kv": level,
        "voltage_method": voltage["voltage_method"],
        "voltage_inferred": voltage["voltage_inferred"],
        "voltage_reference_uid": voltage["voltage_reference_uid"],
        "circuits": circuit_map[level],
        "cables": cable_map[level],
        "circuit_method": circuit_method,
        "cable_method": cable_method,
        "circuit_inferred": circuit_method not in direct,
        "cable_inferred": cable_method not in direct,
    } for level in voltage_order]


def _balanced_allocation(
    total: float,
    voltage_order: list[float],
) -> dict[float, float]:
    """Preserve a reported total, assigning integer remainder in source order."""

    count = len(voltage_order)
    if float(total).is_integer() and total >= count:
        base, remainder = divmod(int(total), count)
        return {
            level: float(base + (index < remainder))
            for index, level in enumerate(voltage_order)
        }
    return {level: float(total) / count for level in voltage_order}


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
        rows.append(_facility_node_row(
            station, source_id, observed_at,
            subclass=subclass,
            classification_method=method,
            substation_raw=station.substation,
        ))
    return rows


def transformer_rows(
    transformers: gpd.GeoDataFrame,
    source_id: str,
    observed_at: object,
) -> list[dict[str, object]]:
    """Create station nodes from OSM transformers used by retained branches."""

    return [_facility_node_row(
        transformer, source_id, observed_at,
        subclass="transformer",
        classification_method="source_tag",
        substation_raw=pd.NA,
    ) for transformer in transformers.itertuples()]


def _facility_node_row(
    feature: object,
    source_id: str,
    observed_at: object,
    *,
    subclass: str,
    classification_method: str,
    substation_raw: object,
) -> dict[str, object]:
    """Normalize one explicit OSM station or transformer feature."""

    return {
        "uid": feature.source_uid,
        "class": "station",
        "subclass": subclass,
        "status": feature.standard_status,
        "voltage_kv": feature.voltage_values,
        "voltage_raw": feature.voltage,
        "voltage_assignment_method": (
            "source_tag" if feature.voltage_values else "unresolved"
        ),
        "voltage_inferred": False,
        "voltage_reference_uid": pd.NA,
        "geometry": feature.geometry,
        "geometry_method": "source_geometry",
        "observed_at": observed_at,
        "valid_from": _partial_time(
            feature.start_date if hasattr(feature, "start_date") else pd.NA
        ),
        "valid_to": _partial_time(
            feature.closing_date if hasattr(feature, "closing_date") else pd.NA
        ),
        "source_id": source_id,
        "source_uid": feature.source_uid.removeprefix("osm:"),
        "name": feature.name,
        "operator": feature.operator,
        "frequency_hz": _numeric(feature.frequency),
        "substation_raw": substation_raw,
        "node_classification_method": classification_method,
        "merged_source_uids": pd.NA,
    }


def voltage_station_subclass(voltages: object, threshold_kv: float) -> str:
    values = list(voltages or [])
    return "transmission" if values and max(values) >= threshold_kv else "distribution"


def fill_missing_node_voltages(
    rows: list[dict[str, object]],
    options: dict[str, object],
) -> None:
    """Fill missing facility voltages from the nearest known node."""

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
    """Merge nodes with identical geometry and core electrical attributes."""

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
    """Validate node semantics, endpoint integrity, voltage, and degree."""

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


def build_electrical_network(
    nodes: gpd.GeoDataFrame,
    branches: gpd.GeoDataFrame,
) -> StandardNetwork:
    """Split geographic nodes into buses and infer local equipment."""

    node_by_uid = nodes.set_index("uid", drop=False)
    bus_rows: list[dict[str, object]] = []
    bus_uid: dict[tuple[str, float, str], str] = {}
    incident: dict[str, set[tuple[float, str]]] = defaultdict(set)
    for row in branches.itertuples(index=False):
        voltage = float(row.voltage_kv[0])
        current = str(row.current_type).upper()
        incident[str(row.from_uid)].add((voltage, current))
        incident[str(row.to_uid)].add((voltage, current))

    for node_uid, systems in sorted(incident.items()):
        node = node_by_uid.loc[node_uid]
        for voltage, current in sorted(systems, key=lambda item: (item[0], item[1])):
            uid = f"{node_uid}:bus:{current.lower()}:{voltage_label(voltage)}"
            bus_uid[(node_uid, voltage, current)] = uid
            bus_rows.append({
                "uid": uid,
                "class": "bus",
                "subclass": f"{node['class']}_bus",
                "status": node["status"],
                "voltage_kv": voltage,
                "current_type": current,
                "geometry": node.geometry,
                "geometry_method": node["geometry_method"],
                "observed_at": node["observed_at"],
                "valid_from": node["valid_from"],
                "valid_to": node["valid_to"],
                "source_id": node["source_id"],
                "source_uid": node["source_uid"],
                "source_node_uid": node_uid,
                "source_node_class": node["class"],
                "source_node_subclass": node["subclass"],
                "name": node.get("name", pd.NA),
                "operator": node.get("operator", pd.NA),
                "bus_assignment_method": "incident_branch_system",
            })

    branch = branches.copy()
    branch_voltage = branch["voltage_kv"].map(lambda values: float(values[0]))
    branch_current = branch["current_type"].astype("string").str.upper()
    branch["source_from_node_uid"] = branch["from_uid"]
    branch["source_to_node_uid"] = branch["to_uid"]
    branch["from_bus_uid"] = [
        bus_uid[(str(uid), voltage, current)]
        for uid, voltage, current in zip(
            branch["from_uid"], branch_voltage, branch_current, strict=True
        )
    ]
    branch["to_bus_uid"] = [
        bus_uid[(str(uid), voltage, current)]
        for uid, voltage, current in zip(
            branch["to_uid"], branch_voltage, branch_current, strict=True
        )
    ]
    branch["source_class"] = branch["class"]
    branch["class"] = "branch"
    branch["voltage_kv"] = branch_voltage
    branch = branch.drop(columns=["from_uid", "to_uid"])

    equipment = {"transformer": [], "converter": []}
    buses_by_node: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in bus_rows:
        buses_by_node[str(row["source_node_uid"])].append(row)
    for node_uid, local_buses in buses_by_node.items():
        if local_buses[0]["source_node_class"] != "station" or len(local_buses) < 2:
            continue
        ordered = sorted(
            local_buses,
            key=lambda row: (float(row["voltage_kv"]), str(row["current_type"])),
        )
        for lower, upper in zip(ordered[:-1], ordered[1:], strict=True):
            currents = {lower["current_type"], upper["current_type"]}
            if currents == {"AC"}:
                kind = "transformer"
            elif currents == {"AC", "DC"}:
                kind = "converter"
            else:
                continue
            node = node_by_uid.loc[node_uid]
            from_current = str(lower["current_type"])
            to_current = str(upper["current_type"])
            from_voltage = float(lower["voltage_kv"])
            to_voltage = float(upper["voltage_kv"])
            row = {
                "uid": (
                    f"{node_uid}:{kind}:{from_current.lower()}:"
                    f"{voltage_label(from_voltage)}:{to_current.lower()}:"
                    f"{voltage_label(to_voltage)}"
                ),
                "class": kind,
                "subclass": (
                    "ac_transformer" if kind == "transformer"
                    else "ac_dc_converter"
                ),
                "status": node["status"],
                "from_bus_uid": lower["uid"],
                "to_bus_uid": upper["uid"],
                "from_voltage_kv": from_voltage,
                "to_voltage_kv": to_voltage,
                "geometry": node.geometry,
                "geometry_method": "inferred_station_geometry",
                "observed_at": node["observed_at"],
                "valid_from": node["valid_from"],
                "valid_to": node["valid_to"],
                "source_id": node["source_id"],
                "source_uid": node["source_uid"],
                "source_node_uid": node_uid,
                "connection_method": "inferred_adjacent_voltage_chain",
                "inferred": True,
            }
            if kind == "converter":
                row["from_current_type"] = from_current
                row["to_current_type"] = to_current
            equipment[kind].append(row)

    bus = _finalize_frame(
        pd.DataFrame(bus_rows),
        schema_id="network.bus",
        string_columns=(
            "current_type", "source_node_uid", "source_node_class",
            "source_node_subclass", "name", "operator", "bus_assignment_method",
        ),
    )
    branch = _finalize_frame(
        branch,
        schema_id="network.branch",
        string_columns=(
            "current_type", "from_bus_uid", "to_bus_uid",
            "source_from_node_uid", "source_to_node_uid", "source_class",
        ),
    )
    transformer = _equipment_frame(equipment["transformer"], "transformer", nodes.crs)
    converter = _equipment_frame(equipment["converter"], "converter", nodes.crs)
    _validate_electrical_network(bus, branch, transformer, converter)
    return StandardNetwork(bus, branch, transformer, converter)


def _equipment_frame(
    rows: list[dict[str, object]],
    kind: str,
    crs: object,
) -> gpd.GeoDataFrame:
    required = list(REQUIRED_COLUMNS[f"network.{kind}"])
    frame = pd.DataFrame(rows)
    if frame.empty:
        frame = pd.DataFrame({column: pd.Series(dtype="object") for column in required})
    string_columns = [
        "from_bus_uid", "to_bus_uid", "source_node_uid", "connection_method",
    ]
    if kind == "converter":
        string_columns.extend(["from_current_type", "to_current_type"])
    return _finalize_frame(
        frame,
        schema_id=f"network.{kind}",
        crs=str(crs),
        string_columns=string_columns,
    )


def _validate_electrical_network(
    bus: gpd.GeoDataFrame,
    branch: gpd.GeoDataFrame,
    transformer: gpd.GeoDataFrame,
    converter: gpd.GeoDataFrame,
) -> None:
    """Enforce bus uniqueness and exact branch endpoint compatibility."""

    if bus[["voltage_kv", "current_type"]].isna().any().any():
        raise ValueError("Every electrical bus must have one voltage and current type.")
    if not bus["current_type"].isin(["AC", "DC"]).all():
        raise ValueError("Electrical bus current_type must be AC or DC.")
    buses = bus.set_index("uid")
    for endpoint in ("from", "to"):
        joined = branch[[f"{endpoint}_bus_uid", "voltage_kv", "current_type"]].join(
            buses[["voltage_kv", "current_type"]],
            on=f"{endpoint}_bus_uid",
            rsuffix="_bus",
        )
        if joined["voltage_kv_bus"].isna().any():
            raise ValueError(f"A branch references a missing {endpoint} bus.")
        if not joined["voltage_kv"].eq(joined["voltage_kv_bus"]).all():
            raise ValueError(f"Branch and {endpoint} bus voltage mismatch.")
        if not joined["current_type"].eq(joined["current_type_bus"]).all():
            raise ValueError(f"Branch and {endpoint} bus current type mismatch.")
    all_uids = pd.concat([
        bus["uid"], branch["uid"], transformer["uid"], converter["uid"]
    ])
    if all_uids.duplicated().any():
        raise ValueError("Network component uid values must be globally unique.")
    for equipment_frame in (transformer, converter):
        endpoints = set(equipment_frame.get("from_bus_uid", ())) | set(
            equipment_frame.get("to_bus_uid", ())
        )
        if not endpoints <= set(bus["uid"]):
            raise ValueError("Inferred equipment references a missing bus.")
    if not converter.empty:
        current_pairs = converter.apply(
            lambda row: {row["from_current_type"], row["to_current_type"]},
            axis=1,
        )
        if not current_pairs.map(lambda pair: pair == {"AC", "DC"}).all():
            raise ValueError("Every inferred converter must connect AC and DC buses.")
        if not converter["subclass"].eq("ac_dc_converter").all():
            raise ValueError("Converter subclass must be ac_dc_converter.")
