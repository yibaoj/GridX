"""Voltage inference and electrical-system allocation for OSM lines."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
import re

import geopandas as gpd
import pandas as pd


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
