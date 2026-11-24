"""Append independently maintained electrical GeoJSON records to an OSM base."""

import geopandas as gpd
import pandas as pd

from .model import StandardNetwork
from .network_model import _validate_electrical_network
from .schema import REQUIRED_COLUMNS, _finalize_frame
from .time import network_validity


def append_network_sources(standardizer, network: StandardNetwork) -> StandardNetwork:
    """Additional source_ids contain class=bus/branch/transformer/converter."""

    tables = {name: [getattr(network, name)] for name in (
        "bus", "branch", "transformer", "converter"
    )}
    for source_id in standardizer.config["source_ids"][1:]:
        frame = gpd.read_file(standardizer.source(source_id)).to_crs("EPSG:4326")
        if "class" not in frame or not frame["class"].isin(tables).all():
            raise ValueError(f"{source_id} must identify each electrical component in class.")
        for name in tables:
            part = frame.loc[frame["class"].eq(name)].copy()
            if part.empty:
                continue
            required = ["uid"]
            if name != "bus":
                required += ["from_bus_uid", "to_bus_uid"]
            required += (
                ["voltage_kv", "current_type"] if name in {"bus", "branch"}
                else ["from_voltage_kv", "to_voltage_kv"]
            )
            if name == "converter":
                required += ["from_current_type", "to_current_type"]
            for column in required:
                if column not in part or part[column].isna().any():
                    raise ValueError(f"{source_id} {name} requires {column}.")
            defaults = {
                "subclass": {"bus": "station_bus", "branch": "ac_overhead_line",
                             "transformer": "ac_transformer", "converter": "ac_dc_converter"}[name],
                "status": pd.NA, "geometry_method": "source_geometry",
                "observed_at": pd.NA, "source_id": source_id,
            }
            if name == "branch":
                defaults["subclass"] = part["current_type"].astype("string").str.lower() + "_overhead_line"
            for column, value in defaults.items():
                if column not in part:
                    part[column] = value
                else:
                    part[column] = part[column].fillna(value)
            part["source_uid"] = part["uid"]
            dates = [network_validity(row) for _, row in part.iterrows()]
            part["valid_from"], part["valid_to"] = zip(*dates)
            for column in REQUIRED_COLUMNS[f"network.{name}"]:
                if column not in part:
                    part[column] = pd.NA
            tables[name].append(part)
    result = StandardNetwork(*(
        _finalize_frame(pd.concat(parts, ignore_index=True), schema_id=f"network.{name}")
        for name, parts in tables.items()
    ))
    _validate_electrical_network(result.bus, result.branch, result.transformer, result.converter)
    return result
