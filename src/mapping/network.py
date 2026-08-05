"""Network connectivity and object-to-bus crosswalks."""

from __future__ import annotations

import warnings

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import xarray as xr

from ..standard import NetworkData


def largest_connected_network(network: NetworkData) -> NetworkData:
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
    result = NetworkData(
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


def map_objects_to_cells(
    objects: gpd.GeoDataFrame,
    cells: gpd.GeoDataFrame,
    *,
    metric_crs: str,
) -> gpd.GeoDataFrame:
    """Assign each object to one cell using a representative point."""

    valid = objects.geometry.notna() & ~objects.geometry.is_empty
    if not valid.all():
        missing = int((~valid).sum())
        raise ValueError(f"Cannot map {missing} objects without valid geometry.")
    points = gpd.GeoDataFrame(
        {"_row": np.arange(len(objects))},
        geometry=_representative_points(objects.geometry),
        crs=objects.crs,
    )
    target = cells[
        ["spatial_uid", "admin_uid", "spatial_level", "geometry"]
    ]
    joined = gpd.sjoin(points, target, how="left", predicate="intersects")
    joined = joined.sort_values(["_row", "spatial_uid"], na_position="last")
    matched = joined.dropna(subset=["spatial_uid"]).drop_duplicates("_row")
    unmatched = points.loc[~points["_row"].isin(matched["_row"])]
    if not unmatched.empty:
        nearest = gpd.sjoin_nearest(
            unmatched.to_crs(metric_crs),
            target.to_crs(metric_crs),
            how="left",
            distance_col="_distance_m",
        ).sort_values(["_row", "spatial_uid"]).drop_duplicates("_row")
        nearest = nearest.to_crs(objects.crs)
        matched = pd.concat([matched, nearest], ignore_index=True)
    lookup = matched.set_index("_row")
    result = objects.copy().reset_index(drop=True)
    result["spatial_uid"] = result.index.map(lookup["spatial_uid"])
    result["admin_uid"] = result.index.map(lookup["admin_uid"])
    result["spatial_level"] = result.index.map(lookup["spatial_level"])
    result["cell_distance_km"] = (
        result.index.map(lookup.get("_distance_m", pd.Series(dtype=float))).fillna(0)
        / 1000
    )
    result.attrs = objects.attrs.copy()
    result.attrs["mapping_dataset_id"] = str(
        objects.attrs.get("standard_dataset_id", "network")
    )
    return result


def map_branches_to_cells(
    branches: gpd.GeoDataFrame,
    cells: gpd.GeoDataFrame,
    *,
    metric_crs: str,
) -> pd.DataFrame:
    """Create a many-to-many branch/cell crosswalk using line overlap."""

    lines = branches[["uid", "geometry"]].rename(columns={"uid": "branch_uid"})
    lines = lines.to_crs(metric_crs)
    lines["branch_length_m"] = lines.geometry.length
    cells = cells[
        ["spatial_uid", "admin_uid", "spatial_level", "geometry"]
    ].to_crs(metric_crs)
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="invalid value encountered in intersection",
            category=RuntimeWarning,
        )
        overlap = gpd.overlay(lines, cells, how="intersection", keep_geom_type=True)
    overlap["overlap_length_km"] = overlap.geometry.length / 1000
    overlap["branch_length_share"] = (
        overlap["overlap_length_km"] * 1000 / overlap["branch_length_m"]
    ).clip(upper=1)
    overlap["mapping_status"] = "intersects"
    result = pd.DataFrame(overlap.drop(columns=["geometry", "branch_length_m"]))
    missing = branches.loc[~branches["uid"].isin(result["branch_uid"]), "uid"]
    if not missing.empty:
        result = pd.concat([
            result,
            pd.DataFrame({
                "branch_uid": missing.to_numpy(),
                "spatial_uid": pd.NA,
                "admin_uid": pd.NA,
                "spatial_level": pd.NA,
                "overlap_length_km": 0.0,
                "branch_length_share": 0.0,
                "mapping_status": "outside_cell_domain",
            }),
        ], ignore_index=True)
    return result


def map_to_buses(
    objects: gpd.GeoDataFrame,
    buses: gpd.GeoDataFrame,
    cells: gpd.GeoDataFrame,
    *,
    source_uid_column: str,
    output_uid_column: str,
    method: str,
    prefer_same_admin: bool,
    metric_crs: str,
    random_seed: int,
    bus_subclasses: list[str] | None = None,
    voltage_preference: str = "high",
) -> pd.DataFrame:
    """Map objects to electrical buses with deterministic tie-breaking."""

    candidates = buses.copy()
    if bus_subclasses:
        candidates = candidates.loc[
            candidates["subclass"].isin(bus_subclasses)
        ].copy()
    if candidates.empty:
        raise ValueError("No eligible network buses remain for mapping.")
    if voltage_preference not in {"high", "low"}:
        raise ValueError("voltage_preference must be 'high' or 'low'.")
    left = objects.copy()
    right = candidates.copy()
    if method == "cell":
        centres = cells.set_index("spatial_uid")["centre_geometry"]
        left["geometry"] = left["spatial_uid"].map(centres)
        right["geometry"] = right["spatial_uid"].map(centres)
        left = left.set_geometry("geometry", crs=cells.crs)
        right = right.set_geometry("geometry", crs=cells.crs)
    elif method != "geometry":
        raise ValueError("Bus mapping method must be 'geometry' or 'cell'.")
    left = left.to_crs(metric_crs).reset_index(drop=True)
    right = right.to_crs(metric_crs).reset_index(drop=True)
    left["_row"] = np.arange(len(left))
    matches = []
    if prefer_same_admin:
        for admin_uid, group in left.groupby("admin_uid", dropna=False, sort=True):
            same_admin = right.loc[right["admin_uid"].eq(admin_uid)]
            matches.append(_nearest(group, same_admin if not same_admin.empty else right))
    else:
        matches.append(_nearest(left, right))
    joined = pd.concat(matches, ignore_index=True)
    preferred = joined.groupby("_row")["voltage_kv"].transform(
        "max" if voltage_preference == "high" else "min"
    )
    joined = joined.loc[joined["voltage_kv"].eq(preferred)].sort_values(
        ["_row", "uid"], kind="stable",
    )
    rng = np.random.default_rng(random_seed)
    selected = pd.DataFrame([
        group.iloc[rng.integers(len(group))]
        for _, group in joined.groupby("_row", sort=True)
    ]).reset_index(drop=True)
    source = left.set_index("_row")
    selected[output_uid_column] = selected["_row"].map(source[source_uid_column])
    selected = selected.rename(columns={"uid": "bus_uid"})
    selected["same_admin"] = selected["admin_uid_left"].eq(
        selected["admin_uid_right"]
    )
    selected["distance_km"] = selected["distance_m"] / 1000
    selected["mapping_method"] = method
    return selected[[
        output_uid_column,
        "bus_uid",
        "mapping_method",
        "distance_km",
        "same_admin",
        "spatial_uid_left",
        "spatial_uid_right",
        "admin_uid_left",
        "admin_uid_right",
    ]].rename(columns={
        "spatial_uid_left": "source_spatial_uid",
        "spatial_uid_right": "bus_spatial_uid",
        "admin_uid_left": "source_admin_uid",
        "admin_uid_right": "bus_admin_uid",
    })


def attach_bus_mapping(
    objects: gpd.GeoDataFrame,
    mapping: pd.DataFrame,
    *,
    source_uid_column: str,
) -> gpd.GeoDataFrame:
    """Add a one-to-one electrical-bus mapping to an object table."""

    if mapping[source_uid_column].duplicated().any():
        raise ValueError(f"Bus mapping is not unique by {source_uid_column!r}.")
    columns = mapping[[
        source_uid_column,
        "bus_uid",
        "mapping_method",
        "distance_km",
        "same_admin",
        "bus_spatial_uid",
        "bus_admin_uid",
    ]].rename(columns={
        source_uid_column: "uid",
        "mapping_method": "bus_mapping_method",
        "distance_km": "bus_distance_km",
        "same_admin": "bus_same_admin",
    })
    result = objects.merge(columns, on="uid", how="left", validate="one_to_one")
    if result["bus_uid"].isna().any():
        raise ValueError("Some mapped objects do not have an electrical bus.")
    result = gpd.GeoDataFrame(result, geometry="geometry", crs=objects.crs)
    result.attrs = objects.attrs.copy()
    return result


def attach_bus_coordinates(
    data: xr.Dataset,
    mapping: pd.DataFrame,
    *,
    source_uid_column: str,
) -> xr.Dataset:
    """Add a one-to-one electrical-bus mapping as coordinates along uid."""

    if mapping[source_uid_column].duplicated().any():
        raise ValueError(f"Bus mapping is not unique by {source_uid_column!r}.")
    indexed = mapping.set_index(source_uid_column).reindex(
        data["uid"].values.astype(str)
    )
    if indexed["bus_uid"].isna().any():
        raise ValueError("Some mapped time-series locations have no electrical bus.")
    return data.assign_coords({
        "bus_uid": ("uid", indexed["bus_uid"].astype(str).to_numpy()),
        "bus_mapping_method": (
            "uid", indexed["mapping_method"].astype(str).to_numpy()
        ),
        "bus_distance_km": ("uid", indexed["distance_km"].to_numpy()),
        "bus_same_admin": ("uid", indexed["same_admin"].to_numpy()),
        "bus_spatial_uid": (
            "uid", indexed["bus_spatial_uid"].astype(str).to_numpy()
        ),
        "bus_admin_uid": (
            "uid", indexed["bus_admin_uid"].astype(str).to_numpy()
        ),
    })


def _nearest(left: gpd.GeoDataFrame, right: gpd.GeoDataFrame) -> pd.DataFrame:
    return pd.DataFrame(gpd.sjoin_nearest(
        left[["_row", "spatial_uid", "admin_uid", "geometry"]],
        right[["uid", "voltage_kv", "spatial_uid", "admin_uid", "geometry"]],
        how="left",
        distance_col="distance_m",
        lsuffix="left",
        rsuffix="right",
    ).drop(columns=["geometry", "index_right"]))


def _representative_points(geometry: gpd.GeoSeries) -> gpd.GeoSeries:
    return gpd.GeoSeries(
        [
            item if item.geom_type == "Point" else item.representative_point()
            for item in geometry
        ],
        index=geometry.index,
        crs=geometry.crs,
    )
