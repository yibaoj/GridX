"""Case-specific network filtering."""

from __future__ import annotations

import networkx as nx

from ..mapping import MappedNetwork
from ..standard import StandardNetwork
from ..standard.time import lifecycle_mask


def largest_connected_network(network: StandardNetwork) -> StandardNetwork:
    """Return the largest subgraph connected by all electrical components."""

    bus_uids = set(network.bus["uid"].dropna())
    components = []
    for name in ("branch", "transformer", "converter"):
        frame = getattr(network, name)
        valid = frame.loc[
            frame["from_bus_uid"].isin(bus_uids)
            & frame["to_bus_uid"].isin(bus_uids)
            & frame["from_bus_uid"].ne(frame["to_bus_uid"])
        ].copy()
        components.append((name, valid))
    graph = nx.Graph()
    for _, frame in components:
        graph.add_edges_from(frame[["from_bus_uid", "to_bus_uid"]].itertuples(
            index=False, name=None,
        ))
    if graph.number_of_nodes() == 0:
        raise ValueError("Network has no valid electrically connected buses.")
    largest = max(nx.connected_components(graph), key=len)
    bus = network.bus.loc[network.bus["uid"].isin(largest)].copy()
    retained = {}
    for name, frame in components:
        retained[name] = frame.loc[
            frame["from_bus_uid"].isin(largest)
            & frame["to_bus_uid"].isin(largest)
        ].copy()
    for frame, source in ((bus, network.bus), *(
        (retained[name], getattr(network, name))
        for name in ("branch", "transformer", "converter")
    )):
        frame["in_largest_connected_graph"] = True
        frame.attrs = source.attrs.copy()
    result = StandardNetwork(
        bus.reset_index(drop=True),
        retained["branch"].reset_index(drop=True),
        retained["transformer"].reset_index(drop=True),
        retained["converter"].reset_index(drop=True),
    )
    validation = nx.Graph()
    validation.add_nodes_from(result.bus["uid"])
    for frame in (result.branch, result.transformer, result.converter):
        validation.add_edges_from(frame[["from_bus_uid", "to_bus_uid"]].itertuples(
            index=False, name=None,
        ))
    if not nx.is_connected(validation):
        raise ValueError("Largest connected network validation failed.")
    return result


def filter_network(network: MappedNetwork, options: dict, *, largest: bool = True, at=None) -> tuple[StandardNetwork, pd.DataFrame]:
    """Filter network components and retain their largest connected graph."""

    minimum = float(options["minimum_voltage_kv"])
    maximum = float(options["maximum_voltage_kv"])
    missing_active = bool(options.get("missing_status_is_active", True))

    bus = network.bus.loc[
        lifecycle_mask(network.bus, at, options["bus_statuses"], missing=missing_active)
        & network.bus["voltage_kv"].between(minimum, maximum, inclusive="both")
    ].copy()
    bus_uids = set(bus["uid"])

    def connected(frame: pd.DataFrame, status_key: str) -> pd.DataFrame:
        return frame.loc[
            lifecycle_mask(frame, at, options[status_key], missing=missing_active)
            & frame["from_bus_uid"].isin(bus_uids)
            & frame["to_bus_uid"].isin(bus_uids)
        ].copy()

    branch = connected(network.branch, "branch_statuses")
    branch = branch.loc[
        branch["voltage_kv"].between(minimum, maximum, inclusive="both")
    ].copy()
    transformer = connected(network.transformer, "transformer_statuses")
    converter = connected(network.converter, "converter_statuses")
    result = StandardNetwork(bus, branch, transformer, converter)
    if largest:
        result = largest_connected_network(result)
    retained = set(result.branch["uid"])
    branch_mapping = network.branch_mapping.loc[
        network.branch_mapping["branch_uid"].isin(retained)
    ].copy()
    return result, branch_mapping
