"""Geographic selection before remapping, without modifying mapped inputs."""
from dataclasses import replace


def select_region(mapped, options):
    ids = list(options.get("spatial_uids", []))
    if not ids:
        return mapped, None
    cells = mapped.spatial
    known = set(cells.spatial_uid) | set(cells.admin_uid)
    missing = set(ids) - known
    if missing:
        raise ValueError(f"Spatial IDs absent from mapped cells: {sorted(missing)}; rebuild mapping with the required administrative levels.")
    selected = cells.loc[cells.spatial_uid.isin(ids) | cells.admin_uid.isin(ids)].copy()
    boundary = selected.geometry.union_all()
    def assets(frame):
        return frame.loc[frame.geometry.representative_point().covered_by(boundary)].copy()
    uids = selected.spatial_uid.values
    load = mapped.load.sel(uid=uids)
    resource = mapped.resource.sel(uid=uids)
    population = mapped.population.loc[mapped.population.spatial_uid.isin(uids)].copy()
    return replace(mapped, spatial=selected, generator=assets(mapped.generator),
        storage=assets(mapped.storage), load=load, resource=resource, population=population), boundary


def largest_internal_network(network):
    """Keep a physical connected component, including a one-bus regional case."""
    import networkx as nx
    from ..standard import StandardNetwork
    graph = nx.Graph()
    graph.add_nodes_from(network.bus.uid)
    for frame in (network.branch, network.transformer, network.converter):
        graph.add_edges_from(frame[["from_bus_uid", "to_bus_uid"]].itertuples(index=False, name=None))
    retained = max(nx.connected_components(graph), key=lambda nodes: (len(nodes), tuple(sorted(nodes))))
    connections = [frame.loc[frame.from_bus_uid.isin(retained) & frame.to_bus_uid.isin(retained)].copy()
                   for frame in (network.branch, network.transformer, network.converter)]
    return StandardNetwork(network.bus.loc[network.bus.uid.isin(retained)].copy(), *connections)
