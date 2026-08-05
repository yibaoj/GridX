"""Convert cleaned geographic OSM connectivity into electrical components."""

from __future__ import annotations

from collections import defaultdict

import geopandas as gpd
import pandas as pd

from .network_voltage import voltage_label
from .schema import NetworkData, REQUIRED_COLUMNS, _finalize_frame


def build_electrical_network(
    nodes: gpd.GeoDataFrame,
    branches: gpd.GeoDataFrame,
) -> NetworkData:
    """Split geographic nodes into single-system buses and infer local equipment."""

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
            kind = (
                "transformer"
                if lower["current_type"] == upper["current_type"] == "AC"
                else "converter"
            )
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
                    else f"{from_current.lower()}_{to_current.lower()}_converter"
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
    return NetworkData(bus, branch, transformer, converter)


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
    for equipment in (transformer, converter):
        endpoints = set(equipment.get("from_bus_uid", ())) | set(
            equipment.get("to_bus_uid", ())
        )
        if not endpoints <= set(bus["uid"]):
            raise ValueError("Inferred equipment references a missing bus.")
