"""OSM power-feature preparation and source-level connectivity."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote

import geopandas as gpd
import pandas as pd
import pyogrio
from shapely import make_valid
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from .base import _Standardizer
from .schema import (
    NetworkData,
    _finalize_frame,
    _numeric,
    _osm_voltages,
    _partial_time,
    _write_geodataframe,
)


class _NetworkStandardizer(_Standardizer):
    _LIFECYCLE_KEYS = (
        ("construction", "construction:power"),
        ("proposed", "proposed:power"),
        ("planned", "planned:power"),
        ("disused", "disused:power"),
        ("abandoned", "abandoned:power"),
        ("demolished", "demolished:power"),
        ("removed", "removed:power"),
        ("razed", "razed:power"),
        ("destroyed", "destroyed:power"),
    )

    def build(self) -> NetworkData:
        source_id = self.config["source_ids"][0]
        pbf_path = self.source(source_id)
        feature_output_prefix = (
            self.manager.project_root / self.options["feature_output_prefix"]
        )
        feature_path = Path(f"{feature_output_prefix}.gpkg")
        preparation_script = (
            self.manager.project_root / self.options["preparation_script"]
        )
        if (
            not feature_path.exists()
            or feature_path.stat().st_mtime
            < max(pbf_path.stat().st_mtime, preparation_script.stat().st_mtime)
        ):
            subprocess.run(
                [
                    str(preparation_script),
                    str(pbf_path),
                    str(feature_output_prefix),
                ],
                cwd=self.manager.project_root,
                check=True,
            )
        reference_path = self.manager.project_root / self.options["reference_cache"]
        self._prepare_reference_cache(pbf_path, reference_path)
        observed_at = subprocess.check_output(
            [
                "osmium",
                "fileinfo",
                "-g",
                "header.option.osmosis_replication_timestamp",
                str(pbf_path),
            ],
            text=True,
        ).strip() or pd.NA
        node_coordinates, way_references = self._read_opl(reference_path)
        features = self._read_features(feature_path)
        lines, stations = self._select_features(features, way_references)
        nodes, branches = self._build_connectivity(
            lines,
            stations,
            node_coordinates,
            way_references,
            source_id,
            observed_at,
        )
        _write_geodataframe(nodes, self.output("nodes"))
        _write_geodataframe(branches, self.output("branches"))
        return NetworkData(nodes, branches)

    def _prepare_reference_cache(self, pbf_path: Path, cache_path: Path) -> None:
        if cache_path.exists() and cache_path.stat().st_mtime >= pbf_path.stat().st_mtime:
            return
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        filters = ["w/power=line", "w/power=cable"]
        for _, key in self._LIFECYCLE_KEYS:
            filters.extend([f"w/{key}=line", f"w/{key}=cable"])
        subprocess.run(
            [
                "osmium",
                "tags-filter",
                "--remove-tags",
                str(pbf_path),
                *filters,
                "-o",
                str(cache_path),
                "--overwrite",
            ],
            check=True,
        )

    @staticmethod
    def _read_opl(
        reference_path: Path,
    ) -> tuple[dict[int, tuple[float, float]], dict[str, list[int]]]:
        nodes: dict[int, tuple[float, float]] = {}
        ways: dict[str, list[int]] = {}
        process = subprocess.Popen(
            ["osmium", "cat", str(reference_path), "-f", "opl"],
            stdout=subprocess.PIPE,
            text=True,
        )
        assert process.stdout is not None
        for line in process.stdout:
            fields = line.rstrip().split(" ")
            object_token = fields[0]
            attributes = {
                field[0]: unquote(field[1:])
                for field in fields[1:]
                if len(field) > 1
            }
            if object_token.startswith("n") and {"x", "y"} <= attributes.keys():
                nodes[int(object_token[1:])] = (
                    float(attributes["x"]),
                    float(attributes["y"]),
                )
            elif object_token.startswith("w") and "N" in attributes:
                ways[f"osm:way:{object_token[1:]}"] = [
                    int(item.lstrip("n"))
                    for item in attributes["N"].split(",")
                    if item
                ]
        if process.wait() != 0:
            raise RuntimeError("osmium failed while exporting OSM node references.")
        return nodes, ways

    def _read_features(self, feature_path: Path) -> gpd.GeoDataFrame:
        required = {
            "@type",
            "@id",
            "power",
            "voltage",
            "frequency",
            "name",
            "operator",
            "circuits",
            "cables",
            "substation",
            "location",
            "start_date",
            "opening_date",
            "closing_date",
            *(key for _, key in self._LIFECYCLE_KEYS),
        }
        available = set(
            pyogrio.read_info(
                feature_path,
                layer=self.options.get("feature_layer", "power_features"),
            )["fields"]
        )
        columns = [column for column in required if column in available]
        frame = gpd.read_file(
            feature_path,
            layer=self.options.get("feature_layer", "power_features"),
            columns=columns,
            engine="pyogrio",
            use_arrow=True,
        )
        frame["source_uid"] = (
            "osm:"
            + frame["@type"].astype("string")
            + ":"
            + frame["@id"].astype("string")
        )
        types, statuses = zip(
            *(self._feature_type_status(row) for _, row in frame.iterrows()),
            strict=True,
        )
        frame["standard_type"] = pd.Series(types, index=frame.index, dtype="string")
        frame["standard_status"] = pd.Series(
            statuses, index=frame.index, dtype="string"
        )
        frame["voltage_values"] = frame["voltage"].map(_osm_voltages)
        return frame

    def _feature_type_status(self, row: pd.Series) -> tuple[object, object]:
        active = row.get("power")
        if active in {"line", "cable", "substation", "converter"}:
            return active, "operating"
        for status, key in self._LIFECYCLE_KEYS:
            value = row.get(key)
            if value in {"line", "cable", "substation", "converter"}:
                return value, status
        return pd.NA, pd.NA

    def _select_features(
        self,
        features: gpd.GeoDataFrame,
        way_references: dict[str, list[int]],
    ) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
        statuses = set(self.options.get("include_statuses", []))
        status_mask = (
            features["standard_status"].isin(statuses)
            if statuses
            else pd.Series(True, index=features.index)
        )
        require_line_voltage = bool(self.options.get("require_line_voltage", True))
        has_voltage = features["voltage_values"].map(bool)
        lines = features.loc[
            status_mask
            & features["standard_type"].isin(["line", "cable"])
            & (has_voltage if require_line_voltage else True)
            & features["source_uid"].isin(way_references)
        ].drop_duplicates("source_uid").copy()
        stations = features.loc[
            status_mask
            & features["standard_type"].isin(["substation", "converter"])
        ].drop_duplicates("source_uid").copy()
        return lines, stations

    def _build_connectivity(
        self,
        lines: gpd.GeoDataFrame,
        stations: gpd.GeoDataFrame,
        node_coordinates: dict[int, tuple[float, float]],
        way_references: dict[str, list[int]],
        source_id: str,
        observed_at: object,
    ) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
        line_records = {
            row.source_uid: row
            for row in lines.itertuples()
            if len(way_references.get(row.source_uid, [])) >= 2
        }
        node_line_voltages: dict[int, dict[float, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        terminal_lines: dict[int, set[str]] = defaultdict(set)
        for line_uid, row in line_records.items():
            references = way_references[line_uid]
            for node_id in references:
                for voltage in row.voltage_values:
                    node_line_voltages[node_id][voltage].add(line_uid)
            terminal_lines[references[0]].add(line_uid)
            terminal_lines[references[-1]].add(line_uid)

        shared_nodes = {
            node_id
            for node_id, by_voltage in node_line_voltages.items()
            if any(len(line_uids) >= 2 for line_uids in by_voltage.values())
        }
        terminal_representative = self._cluster_terminals(
            terminal_lines,
            line_records,
            node_coordinates,
        )
        candidate_nodes = {
            node_id
            for line_uid in line_records
            for node_id in way_references[line_uid]
            if node_id in shared_nodes
            or node_id in {
                way_references[line_uid][0],
                way_references[line_uid][-1],
            }
        }
        station_by_node = self._match_stations(
            candidate_nodes,
            node_coordinates,
            stations,
        )

        branch_rows = []
        for line_uid, line in line_records.items():
            references = way_references[line_uid]
            split_indices = sorted({
                0,
                len(references) - 1,
                *(
                    index
                    for index, node_id in enumerate(references)
                    if node_id in shared_nodes or node_id in station_by_node
                ),
            })
            current_type = self._current_type(line.frequency, line.name)
            for start_index, end_index in zip(
                split_indices[:-1], split_indices[1:], strict=True
            ):
                segment_refs = references[start_index : end_index + 1]
                coordinates = [
                    node_coordinates[node_id]
                    for node_id in segment_refs
                    if node_id in node_coordinates
                ]
                if len(coordinates) != len(segment_refs) or len(coordinates) < 2:
                    continue
                start_raw, end_raw = segment_refs[0], segment_refs[-1]
                start_node = terminal_representative.get(start_raw, start_raw)
                end_node = terminal_representative.get(end_raw, end_raw)
                from_uid = station_by_node.get(start_raw, f"osm:node:{start_node}")
                to_uid = station_by_node.get(end_raw, f"osm:node:{end_node}")
                if from_uid == to_uid:
                    continue
                branch_rows.append({
                    "uid": (
                        f"{line_uid}:node:{start_raw}:node:{end_raw}"
                    ),
                    "class": line.standard_type,
                    "subclass": (
                        f"{current_type.lower()}_overhead_line"
                        if line.standard_type == "line"
                        else f"{current_type.lower()}_cable"
                    ),
                    "status": line.standard_status,
                    "voltage_kv": line.voltage_values,
                    "from_uid": from_uid,
                    "to_uid": to_uid,
                    "length_km": pd.NA,
                    "geometry": LineString(coordinates),
                    "geometry_method": "source_geometry",
                    "observed_at": observed_at,
                    "valid_from": _partial_time(
                        line.start_date if hasattr(line, "start_date") else pd.NA
                    ),
                    "valid_to": _partial_time(
                        line.closing_date if hasattr(line, "closing_date") else pd.NA
                    ),
                    "source_id": source_id,
                    "source_uid": line_uid.removeprefix("osm:"),
                    "name": line.name,
                    "operator": line.operator,
                    "current_type": current_type,
                    "frequency_hz": _numeric(line.frequency),
                    "circuits": _numeric(line.circuits),
                    "cables": _numeric(line.cables),
                })

        branches = _finalize_frame(
            pd.DataFrame(branch_rows),
            schema_id="network.branches",
            string_columns=(
                "from_uid",
                "to_uid",
                "name",
                "operator",
                "current_type",
            ),
        )
        for column in ("frequency_hz", "circuits", "cables", "length_km"):
            branches[column] = pd.to_numeric(
                branches[column], errors="coerce"
            ).astype("Float64")
        branches_metric = branches.to_crs(self.options["metric_crs"])
        branches["length_km"] = branches_metric.length / 1000

        node_rows = self._station_rows(stations, source_id, observed_at)
        incident_voltage: dict[str, set[float]] = defaultdict(set)
        for branch in branches.itertuples():
            for uid in (branch.from_uid, branch.to_uid):
                incident_voltage[uid].update(branch.voltage_kv or [])
        existing = {row["uid"] for row in node_rows}
        for uid, voltages in incident_voltage.items():
            if uid in existing:
                continue
            node_id = int(uid.rsplit(":", 1)[1])
            node_rows.append({
                "uid": uid,
                "class": "junction",
                "subclass": "topological_junction",
                "status": pd.NA,
                "voltage_kv": sorted(voltages),
                "geometry": Point(node_coordinates[node_id]),
                "geometry_method": "source_geometry",
                "observed_at": observed_at,
                "valid_from": pd.NA,
                "valid_to": pd.NA,
                "source_id": source_id,
                "source_uid": f"node:{node_id}",
                "name": pd.NA,
                "operator": pd.NA,
                "frequency_hz": pd.NA,
            })
        for row in node_rows:
            if row["uid"] in incident_voltage:
                row["voltage_kv"] = sorted(
                    set(row.get("voltage_kv") or [])
                    | incident_voltage[row["uid"]]
                )
        nodes = _finalize_frame(
            pd.DataFrame(node_rows),
            schema_id="network.nodes",
            string_columns=("name", "operator"),
        )
        nodes["frequency_hz"] = pd.to_numeric(
            nodes["frequency_hz"], errors="coerce"
        ).astype("Float64")
        missing = (
            set(branches["from_uid"]) | set(branches["to_uid"])
        ).difference(nodes["uid"])
        if missing:
            raise ValueError(f"Network branches reference missing nodes: {len(missing)}")
        if set(nodes["uid"]) & set(branches["uid"]):
            raise ValueError("Network node and branch uid values must be globally unique.")
        return nodes, branches

    def _cluster_terminals(
        self,
        terminal_lines: dict[int, set[str]],
        line_records: dict[str, object],
        coordinates: dict[int, tuple[float, float]],
    ) -> dict[int, int]:
        tolerance = float(self.options.get("line_endpoint_tolerance_m", 0))
        terminal_ids = [node for node in terminal_lines if node in coordinates]
        parents = {node: node for node in terminal_ids}
        if tolerance <= 0 or len(terminal_ids) < 2:
            return parents

        points = gpd.GeoSeries(
            [Point(coordinates[node]) for node in terminal_ids],
            crs="EPSG:4326",
        ).to_crs(self.options["metric_crs"])
        tree = STRtree(points.array)
        pairs = tree.query(points.array, predicate="dwithin", distance=tolerance)

        def find(node: int) -> int:
            while parents[node] != node:
                parents[node] = parents[parents[node]]
                node = parents[node]
            return node

        def union(left: int, right: int) -> None:
            left_root, right_root = find(left), find(right)
            representative = min(left_root, right_root)
            parents[left_root] = representative
            parents[right_root] = representative

        for left_index, right_index in zip(*pairs, strict=True):
            left, right = terminal_ids[left_index], terminal_ids[right_index]
            if left >= right:
                continue
            left_voltages = {
                voltage
                for line_uid in terminal_lines[left]
                for voltage in line_records[line_uid].voltage_values
            }
            right_voltages = {
                voltage
                for line_uid in terminal_lines[right]
                for voltage in line_records[line_uid].voltage_values
            }
            if left_voltages & right_voltages:
                union(left, right)
        return {node: find(node) for node in terminal_ids}

    def _match_stations(
        self,
        node_ids: set[int],
        coordinates: dict[int, tuple[float, float]],
        stations: gpd.GeoDataFrame,
    ) -> dict[int, str]:
        usable = [node for node in node_ids if node in coordinates]
        if not usable or stations.empty:
            return {}
        node_points = gpd.GeoDataFrame(
            {"node_id": usable},
            geometry=[Point(coordinates[node]) for node in usable],
            crs="EPSG:4326",
        ).to_crs(self.options["metric_crs"])
        station_geometry = stations[["source_uid", "geometry"]].copy()
        station_geometry.geometry = station_geometry.geometry.map(
            lambda geometry: make_valid(geometry) if geometry is not None else None
        )
        station_geometry = station_geometry.to_crs(self.options["metric_crs"])
        matches = gpd.sjoin_nearest(
            node_points,
            station_geometry,
            how="left",
            max_distance=float(self.options["station_tolerance_m"]),
            distance_col="distance_m",
        ).dropna(subset=["source_uid"])
        matches = matches.sort_values(
            ["node_id", "distance_m", "source_uid"]
        ).drop_duplicates("node_id")
        return dict(zip(matches["node_id"], matches["source_uid"], strict=True))

    @staticmethod
    def _current_type(frequency: object, name: object) -> str:
        frequencies = re.split(r"[;,/]", "" if pd.isna(frequency) else str(frequency))
        if any(value.strip() in {"0", "0.0"} for value in frequencies):
            return "DC"
        if re.search(r"直流|HVDC", "" if pd.isna(name) else str(name), re.I):
            return "DC"
        return "AC"

    @staticmethod
    def _station_rows(
        stations: gpd.GeoDataFrame,
        source_id: str,
        observed_at: object,
    ) -> list[dict[str, object]]:
        rows = []
        for station in stations.itertuples():
            station_class = (
                "" if pd.isna(station.substation) else str(station.substation).lower()
            )
            if station.standard_type == "converter" or "converter" in station_class:
                subclass = "converter_station"
            elif station_class == "transmission":
                subclass = "transmission_substation"
            elif station_class == "distribution":
                subclass = "distribution_substation"
            else:
                subclass = pd.NA
            rows.append({
                "uid": station.source_uid,
                "class": "station",
                "subclass": subclass,
                "status": station.standard_status,
                "voltage_kv": station.voltage_values,
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
            })
        return rows
