"""Case-specific network filtering."""

from __future__ import annotations

import pandas as pd

from ..mapping import MappedNetwork
from ..mapping.network import largest_connected_network
from ..standard import NetworkData


def filter_network(network: MappedNetwork, options: dict) -> tuple[NetworkData, pd.DataFrame]:
    """Filter network components and retain their largest connected graph."""

    minimum = float(options["minimum_voltage_kv"])
    maximum = float(options["maximum_voltage_kv"])
    missing_active = bool(options.get("missing_status_is_active", True))

    bus = network.bus.loc[
        _status_mask(network.bus, options["bus_statuses"], missing_active)
        & network.bus["voltage_kv"].between(minimum, maximum, inclusive="both")
    ].copy()
    bus_uids = set(bus["uid"])

    def connected(frame: pd.DataFrame, status_key: str) -> pd.DataFrame:
        return frame.loc[
            _status_mask(frame, options[status_key], missing_active)
            & frame["from_bus_uid"].isin(bus_uids)
            & frame["to_bus_uid"].isin(bus_uids)
        ].copy()

    branch = connected(network.branch, "branch_statuses")
    branch = branch.loc[
        branch["voltage_kv"].between(minimum, maximum, inclusive="both")
    ].copy()
    transformer = connected(network.transformer, "transformer_statuses")
    converter = connected(network.converter, "converter_statuses")
    result = largest_connected_network(
        NetworkData(bus, branch, transformer, converter)
    )
    retained = set(result.branch["uid"])
    branch_mapping = network.branch_mapping.loc[
        network.branch_mapping["branch_uid"].isin(retained)
    ].copy()
    return result, branch_mapping


def _status_mask(
    frame: pd.DataFrame,
    allowed: list[str],
    missing_active: bool,
) -> pd.Series:
    status = frame["status"].astype("string").str.lower()
    mask = status.isin({str(value).lower() for value in allowed})
    return mask | (status.isna() & missing_active)
