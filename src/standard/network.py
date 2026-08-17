"""OSM power-feature preparation and source-level connectivity."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import subprocess
from urllib.parse import unquote

import geopandas as gpd
import pandas as pd
import pyogrio
from shapely import make_valid
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from .base import _Standardizer
from .network_model import (
    build_electrical_network,
    current_type,
    deduplicate_node_rows,
    fill_missing_node_voltages,
    line_systems,
    resolve_line_voltages,
    station_rows,
    tag_numbers,
    transformer_rows,
    validate_nodes,
    voltage_label,
    voltage_station_subclass,
)
from .model import StandardNetwork
from .schema import (
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

    def build(self) -> StandardNetwork:
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
        lines, stations, transformers = self._select_features(
            features, way_references
        )
        nodes, branches = self._build_connectivity(
            lines,
            stations,
            transformers,
            node_coordinates,
            way_references,
            source_id,
            observed_at,
        )
        network = build_electrical_network(nodes, branches)
        for component in ("bus", "branch", "transformer", "converter"):
            _write_geodataframe(
                getattr(network, component), self.output() / f"{component}.parquet"
            )
        return network

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
        if active in {"line", "cable", "substation", "converter", "transformer"}:
            return active, "operating"
        for status, key in self._LIFECYCLE_KEYS:
            value = row.get(key)
            if value in {"line", "cable", "substation", "converter", "transformer"}:
                return value, status
        return pd.NA, pd.NA

    def _select_features(
        self,
        features: gpd.GeoDataFrame,
        way_references: dict[str, list[int]],
    ) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
        statuses = set(self.options.get("include_statuses", []))
        status_mask = (
            features["standard_status"].isin(statuses)
            if statuses
            else pd.Series(True, index=features.index)
        )
        require_line_voltage = bool(self.options.get("require_line_voltage", True))
        has_voltage = features["voltage_values"].map(bool)
        anomalous_frequencies = {
            float(value)
            for value in self.options.get("anomalous_frequencies_hz", ())
        }
        accepted_frequency = features["frequency"].map(
            lambda value: anomalous_frequencies.isdisjoint(
                tag_numbers(value)
            )
        )
        lines = features.loc[
            status_mask
            & features["standard_type"].isin(["line", "cable"])
            & (has_voltage if require_line_voltage else True)
            & accepted_frequency
            & features["source_uid"].isin(way_references)
        ].drop_duplicates("source_uid").copy()
        stations = features.loc[
            status_mask
            & features["standard_type"].isin(["substation", "converter"])
        ].drop_duplicates("source_uid").copy()
        transformers = features.loc[
            status_mask
            & features["standard_type"].eq("transformer")
            & features["@type"].eq("node")
        ].drop_duplicates("source_uid").copy()
        return lines, stations, transformers

    def _build_connectivity(
        self,
        lines: gpd.GeoDataFrame,
        stations: gpd.GeoDataFrame,
        transformers: gpd.GeoDataFrame,
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
        line_voltages = resolve_line_voltages(
            line_records, way_references, self.options
        )
        systems_by_line = {
            line_uid: line_systems(row, line_voltages[line_uid], self.options)
            for line_uid, row in line_records.items()
        }
        node_line_voltages: dict[int, dict[float, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        terminal_voltages: dict[int, set[float]] = defaultdict(set)
        for line_uid, row in line_records.items():
            references = way_references[line_uid]
            for node_id in references:
                for system in systems_by_line[line_uid]:
                    node_line_voltages[node_id][system["voltage_kv"]].add(line_uid)
            for system in systems_by_line[line_uid]:
                terminal_voltages[references[0]].add(system["voltage_kv"])
                terminal_voltages[references[-1]].add(system["voltage_kv"])

        shared_node_voltages = {
            (node_id, voltage)
            for node_id, by_voltage in node_line_voltages.items()
            for voltage, line_uids in by_voltage.items()
            if len(line_uids) >= 2
        }
        terminal_representative = self._cluster_terminals(
            terminal_voltages,
            node_coordinates,
        )
        transformer_by_node = {
            int(row["@id"]): row["source_uid"]
            for _, row in transformers.iterrows()
        }
        candidate_nodes = {
            node_id
            for line_uid in line_records
            for node_id in way_references[line_uid]
            if any(
                (node_id, system["voltage_kv"]) in shared_node_voltages
                for system in systems_by_line[line_uid]
            )
            or node_id in transformer_by_node
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
        junction_source_nodes: dict[str, int] = {}
        used_transformers: set[str] = set()
        station_uids = set(station_by_node.values())
        transformer_uids = set(transformer_by_node.values())
        for line_uid, line in line_records.items():
            references = way_references[line_uid]
            branch_current_type = current_type(line.frequency, line.name)
            for system in systems_by_line[line_uid]:
                voltage = system["voltage_kv"]
                split_indices = sorted({
                    0,
                    len(references) - 1,
                    *(
                        index
                        for index, node_id in enumerate(references)
                        if (node_id, voltage) in shared_node_voltages
                        or node_id in station_by_node
                        or node_id in transformer_by_node
                    ),
                })
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
                    start_node = terminal_representative.get(
                        (start_raw, voltage), start_raw
                    )
                    end_node = terminal_representative.get(
                        (end_raw, voltage), end_raw
                    )
                    from_uid = self._endpoint_uid(
                        start_raw,
                        start_node,
                        voltage,
                        station_by_node,
                        transformer_by_node,
                    )
                    to_uid = self._endpoint_uid(
                        end_raw,
                        end_node,
                        voltage,
                        station_by_node,
                        transformer_by_node,
                    )
                    if from_uid == to_uid:
                        continue
                    for uid, representative in (
                        (from_uid, start_node),
                        (to_uid, end_node),
                    ):
                        if uid in transformer_uids:
                            used_transformers.add(uid)
                        elif uid not in station_uids:
                            junction_source_nodes[uid] = representative
                    branch_rows.append({
                        "uid": (
                            f"{line_uid}:voltage:{voltage_label(voltage)}:"
                            f"node:{start_raw}:node:{end_raw}"
                        ),
                        "class": line.standard_type,
                        "subclass": (
                            f"{branch_current_type.lower()}_overhead_line"
                            if line.standard_type == "line"
                            else f"{branch_current_type.lower()}_cable"
                        ),
                        "status": line.standard_status,
                        "voltage_kv": [voltage],
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
                        "current_type": branch_current_type,
                        "frequency_hz": _numeric(line.frequency),
                        "circuits": system["circuits"],
                        "cables": system["cables"],
                        "voltage_raw": line.voltage,
                        "voltage_assignment_method": system["voltage_method"],
                        "voltage_inferred": system["voltage_inferred"],
                        "voltage_reference_uid": system["voltage_reference_uid"],
                        "circuits_raw": line.circuits,
                        "cables_raw": line.cables,
                        "circuit_allocation_method": system["circuit_method"],
                        "cable_allocation_method": system["cable_method"],
                        "circuit_inferred": system["circuit_inferred"],
                        "cable_inferred": system["cable_inferred"],
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
                "voltage_raw",
                "voltage_assignment_method",
                "voltage_reference_uid",
                "circuits_raw",
                "cables_raw",
                "circuit_allocation_method",
                "cable_allocation_method",
            ),
        )
        for column in ("frequency_hz", "circuits", "cables", "length_km"):
            branches[column] = pd.to_numeric(
                branches[column], errors="coerce"
            ).astype("Float64")
        for column in ("voltage_inferred", "circuit_inferred", "cable_inferred"):
            branches[column] = branches[column].astype("boolean")
        branches_metric = branches.to_crs(self.options["metric_crs"])
        branches["length_km"] = branches_metric.length / 1000

        node_rows = station_rows(
            stations, source_id, observed_at, self.options
        )
        node_rows.extend(transformer_rows(
            transformers.loc[transformers["source_uid"].isin(used_transformers)],
            source_id,
            observed_at,
        ))
        incident_voltage: dict[str, set[float]] = defaultdict(set)
        incident_branches: dict[str, list[str]] = defaultdict(list)
        for branch in branches.itertuples():
            for uid in (branch.from_uid, branch.to_uid):
                incident_voltage[uid].update(branch.voltage_kv or [])
                incident_branches[uid].append(branch.uid)
        existing = {row["uid"] for row in node_rows}
        threshold = float(
            self.options["transmission_voltage_threshold_kv"]
        )
        for uid, voltages in incident_voltage.items():
            if uid in existing:
                continue
            node_id = junction_source_nodes[uid]
            is_junction = len(set(incident_branches[uid])) >= 2
            node_rows.append({
                "uid": uid,
                "class": "junction",
                "subclass": "same_voltage" if is_junction else "line_terminal",
                "status": pd.NA,
                "voltage_kv": sorted(voltages),
                "voltage_raw": pd.NA,
                "voltage_assignment_method": "inferred_incident_branches",
                "voltage_inferred": True,
                "voltage_reference_uid": sorted(incident_branches[uid])[0],
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
                "substation_raw": pd.NA,
                "node_classification_method": (
                    "same_voltage_branch_connection"
                    if is_junction else "line_terminal"
                ),
                "merged_source_uids": pd.NA,
            })
        for row in node_rows:
            source_values = set(row.get("voltage_kv") or [])
            connected_values = incident_voltage.get(row["uid"], set())
            if source_values and connected_values.difference(source_values):
                row["voltage_kv"] = sorted(source_values | connected_values)
                row["voltage_assignment_method"] = (
                    "source_tag_augmented_incident_branches"
                )
                row["voltage_inferred"] = True
                row["voltage_reference_uid"] = sorted(
                    incident_branches[row["uid"]]
                )[0]
            elif not source_values and connected_values:
                row["voltage_kv"] = sorted(connected_values)
                row["voltage_assignment_method"] = "inferred_incident_branches"
                row["voltage_inferred"] = True
                row["voltage_reference_uid"] = sorted(
                    incident_branches[row["uid"]]
                )[0]
        fill_missing_node_voltages(node_rows, self.options)
        for row in node_rows:
            if row.get("node_classification_method") == "inferred_voltage_threshold":
                row["subclass"] = voltage_station_subclass(
                    row.get("voltage_kv"), threshold
                )
        node_rows = deduplicate_node_rows(node_rows, branches)
        nodes = _finalize_frame(
            pd.DataFrame(node_rows),
            schema_id="network.nodes",
            string_columns=(
                "name", "operator", "voltage_raw",
                "voltage_assignment_method", "voltage_reference_uid",
                "substation_raw", "node_classification_method",
                "merged_source_uids",
            ),
        )
        nodes["frequency_hz"] = pd.to_numeric(
            nodes["frequency_hz"], errors="coerce"
        ).astype("Float64")
        nodes["voltage_inferred"] = nodes["voltage_inferred"].astype("boolean")
        if set(nodes["uid"]) & set(branches["uid"]):
            raise ValueError("Network node and branch uid values must be globally unique.")
        validate_nodes(nodes, branches)
        anomalous = set(
            float(value)
            for value in self.options.get("anomalous_frequencies_hz", ())
        )
        if branches["frequency_hz"].dropna().isin(anomalous).any():
            raise ValueError("Network contains an anomalous configured frequency.")
        return nodes, branches

    def _cluster_terminals(
        self,
        terminal_voltages: dict[int, set[float]],
        coordinates: dict[int, tuple[float, float]],
    ) -> dict[tuple[int, float], int]:
        tolerance = float(self.options.get("line_endpoint_tolerance_m", 0))
        representatives = {}
        voltages = sorted({
            voltage for values in terminal_voltages.values() for voltage in values
        })
        for voltage in voltages:
            terminal_ids = [
                node for node, values in terminal_voltages.items()
                if voltage in values and node in coordinates
            ]
            parents = {node: node for node in terminal_ids}

            def find(node: int) -> int:
                while parents[node] != node:
                    parents[node] = parents[parents[node]]
                    node = parents[node]
                return node

            if tolerance > 0 and len(terminal_ids) >= 2:
                points = gpd.GeoSeries(
                    [Point(coordinates[node]) for node in terminal_ids],
                    crs="EPSG:4326",
                ).to_crs(self.options["metric_crs"])
                pairs = STRtree(points.array).query(
                    points.array,
                    predicate="dwithin",
                    distance=tolerance,
                )
                for left_index, right_index in zip(*pairs, strict=True):
                    left, right = terminal_ids[left_index], terminal_ids[right_index]
                    if left >= right:
                        continue
                    left_root, right_root = find(left), find(right)
                    representative = min(left_root, right_root)
                    parents[left_root] = representative
                    parents[right_root] = representative
            representatives.update({
                (node, voltage): find(node) for node in terminal_ids
            })
        return representatives

    @staticmethod
    def _endpoint_uid(
        raw_node: int,
        representative: int,
        voltage: float,
        station_by_node: dict[int, str],
        transformer_by_node: dict[int, str],
    ) -> str:
        if raw_node in station_by_node:
            return station_by_node[raw_node]
        if raw_node in transformer_by_node:
            return transformer_by_node[raw_node]
        return (
            f"osm:node:{representative}:voltage:{voltage_label(voltage)}"
        )

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
