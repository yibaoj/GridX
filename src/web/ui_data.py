"""Derive dashboard caches only from the public, locally exported web payloads."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import tempfile
from .contract import SCHEMA_VERSION, validate_payload


def read_payload(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    validate_payload(str(path), payload)
    return payload


def polygons(geometry: dict | None) -> list:
    if not geometry:
        return []
    kind = geometry.get("type")
    if kind == "Polygon":
        return [geometry["coordinates"]]
    if kind == "MultiPolygon":
        return geometry["coordinates"]
    if kind == "GeometryCollection":
        return [part for child in geometry["geometries"] for part in polygons(child)]
    return []


def positions(geometry: dict | None):
    if not geometry:
        return
    if geometry.get("type") == "GeometryCollection":
        for child in geometry["geometries"]:
            yield from positions(child)
        return

    def walk(value):
        if value and isinstance(value[0], (int, float)):
            if len(value) >= 2 and all(math.isfinite(x) for x in value[:2]):
                yield value[:2]
        else:
            for child in value:
                yield from walk(child)

    yield from walk(geometry.get("coordinates", []))


def bounds(points) -> tuple:
    points = list(points)
    if not points:
        return (math.inf, math.inf, -math.inf, -math.inf)
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def overlap(a, b) -> bool:
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def on_segment(point, a, b) -> bool:
    cross = (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])
    return (abs(cross) <= 1e-10 and
            min(a[0], b[0]) - 1e-10 <= point[0] <= max(a[0], b[0]) + 1e-10 and
            min(a[1], b[1]) - 1e-10 <= point[1] <= max(a[1], b[1]) + 1e-10)


def in_ring(point, ring) -> int:
    inside = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        if on_segment(point, a, b):
            return 2
        if ((a[1] > point[1]) != (b[1] > point[1]) and
                point[0] < (b[0] - a[0]) * (point[1] - a[1]) / (b[1] - a[1]) + a[0]):
            inside = not inside
    return int(inside)


def in_polygon(point, rings) -> bool:
    outer = in_ring(point, rings[0])
    if not outer:
        return False
    if outer == 2:
        return True
    for hole in rings[1:]:
        result = in_ring(point, hole)
        if result:
            return result == 2
    return True


def interior_point(rings):
    box = bounds(rings[0])
    y = (box[1] + box[3]) / 2
    crosses = []
    for a, b in zip(rings[0], rings[0][1:] + rings[0][:1]):
        if (a[1] > y) != (b[1] > y):
            crosses.append(a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]))
    crosses.sort()
    for left, right in zip(crosses, crosses[1:]):
        point = [(left + right) / 2, y]
        if right > left and in_polygon(point, rings):
            return point
    return None


def segments_intersect(a, b, c, d) -> bool:
    if not overlap(bounds([a, b]), bounds([c, d])):
        return False

    def cross(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    x, y, z, w = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
    if x * y < 0 and z * w < 0:
        return True
    return any((on_segment(c, a, b), on_segment(d, a, b),
                on_segment(a, c, d), on_segment(b, c, d)))


def line_intersects(line, rings) -> bool:
    if any(in_polygon(point, rings) for point in line):
        return True
    return any(segments_intersect(a, b, c, d)
               for a, b in zip(line, line[1:])
               for ring in rings for c, d in zip(ring, ring[1:]))


def feature_id(feature: dict) -> str:
    props = feature.get("properties") or {}
    return str(props.get("location_uid") or props.get("spatial_uid") or
               props.get("uid") or feature.get("id") or "")


def unique_features(features: list) -> list:
    result = {}
    for index, feature in enumerate(features):
        result.setdefault(feature_id(feature) or str(index), feature)
    return list(result.values())


def region_name(feature: dict, boundary: dict) -> str:
    props = feature["properties"]
    if props.get("name"):
        return str(props["name"])
    regions = [item for item in boundary.get("features", [])
               if item["properties"].get("spatial_level") == "province"]
    direct = [item for item in regions if feature_id(item) == feature_id(feature)]
    if direct:
        return str(direct[0]["properties"]["name"])
    for rings in polygons(feature.get("geometry")):
        point = interior_point(rings)
        if point is None:
            continue
        owners = [item for item in regions
                  if any(in_polygon(point, part) for part in polygons(item.get("geometry")))]
        if len(owners) == 1:
            return str(owners[0]["properties"]["name"])
    return feature_id(feature)


def regional_summary(overview: dict, payloads: dict) -> dict:
    load = payloads["layers/standard/load.json.gz"]
    generator = payloads["layers/standard/generator.json.gz"]
    network = payloads["layers/standard/network.json.gz"]
    application = payloads.get("application/uc.json.gz", {"time": [], "series": [], "summary": {}})
    shapes = {feature_id(feature): [(rings, bounds(rings[0]))
              for rings in polygons(feature.get("geometry"))]
              for feature in load["geojson"]["features"]}
    empty = dict(installed_capacity_mw=0.0, new_energy_capacity_mw=0.0,
                 annual_energy_twh=0.0, peak_load_mw=None, uhv_branch_count=0)
    regions = {p["id"]: {**empty, "id": p["id"], "name": p["name"],
               "annual_energy_twh": p["value"], "generator_locations": 0}
               for p in overview["provinces"]}
    national = {**empty, "annual_energy_twh": overview["annual_energy_twh"]}
    coverage = dict(unassigned_locations=0, ambiguous_locations=0, unassigned_capacity_mw=0.0)
    features = unique_features(generator["geojson"]["features"])
    for feature in features:
        geometry, props = feature.get("geometry") or {}, feature["properties"]
        capacity = float(props.get("total_mw") or 0)
        new_energy = sum(float(props.get("breakdown", {}).get(key) or 0) for key in ("wind", "solar"))
        national["installed_capacity_mw"] += capacity
        national["new_energy_capacity_mw"] += new_energy
        point = geometry.get("coordinates") if geometry.get("type") == "Point" else None
        owners = []
        if point and all(math.isfinite(x) for x in point[:2]):
            point_box = [point[0], point[1], point[0], point[1]]
            owners = [key for key in regions if any(overlap(point_box, box) and in_polygon(point, rings)
                      for rings, box in shapes.get(key, []))]
        if len(owners) == 1:
            region = regions[owners[0]]
            region["installed_capacity_mw"] += capacity
            region["new_energy_capacity_mw"] += new_energy
            region["generator_locations"] += 1
        else:
            coverage["unassigned_locations"] += 1
            coverage["unassigned_capacity_mw"] += capacity
            coverage["ambiguous_locations"] += int(len(owners) > 1)
    branches = {key: set() for key in regions}
    national_branches = set()
    for feature in network["geojson"]["features"]:
        props = feature["properties"]
        voltage = abs(float(props.get("voltage_kv") or 0))
        if props.get("feature_kind") != "branch" or not (
                props.get("current_type") == "AC" and voltage >= 1000 or
                props.get("current_type") == "DC" and voltage >= 800):
            continue
        geometry = feature.get("geometry") or {}
        lines = ([geometry["coordinates"]] if geometry.get("type") == "LineString" else
                 geometry.get("coordinates", []) if geometry.get("type") == "MultiLineString" else [])
        identifier = feature_id(feature)
        if not identifier or not lines:
            continue
        national_branches.add(identifier)
        for line in lines:
            box = bounds(line)
            for key in regions:
                if identifier not in branches[key] and any(overlap(box, region_box) and line_intersects(line, rings)
                        for rings, region_box in shapes.get(key, [])):
                    branches[key].add(identifier)
    national["uhv_branch_count"] = len(national_branches)
    for key, region in regions.items():
        region["uhv_branch_count"] = len(branches[key])
    demand = [sum(series["values"][index] for series in application["series"] if series["kind"] == "load")
              for index in range(len(application["time"]))]
    peak = max(demand) if demand else None
    return dict(schema_version=SCHEMA_VERSION, year=overview["year"], national=national, provinces=regions,
                coverage=coverage, definitions=dict(
                    capacity="Geolocated standard-layer capacity, assigned by exported province polygons.",
                    annual_peak="Unavailable in the exported package; never replaced by a simulation peak.",
                    uhv="Unique AC branches >= 1000 kV or DC branches >= 800 kV, intersecting each province."),
                simulation=dict(installed_capacity_mw=payloads.get("layers/case/generator.json.gz", {}).get("summary", {}).get("value"),
                    maximum_load_mw=peak, peak_timestamp=application["time"][demand.index(peak)] if demand else None,
                    start=application["time"][0] if demand else None, end=application["time"][-1] if demand else None,
                    minimum_voltage_kv=application["summary"].get("case_minimum_voltage_kv")))


def dashboard_summary(payloads: dict) -> dict:
    manifest = payloads["manifest.json.gz"]
    boundary = payloads.get("boundary/common.json.gz", {})
    result = dict(schema_version=SCHEMA_VERSION, year=manifest["year"], historical=True,
                  generated_at=datetime.now(timezone.utc).isoformat(), catalog=[], metrics={},
                  capacity_mix=[], provinces=[], province_names={})
    for relative, payload in payloads.items():
        if not relative.startswith("layers/"):
            continue
        parts = relative.removesuffix(".json.gz").split("/")
        stage, layer = parts[1:3]
        features, summary = payload["geojson"]["features"], payload["summary"]
        result["catalog"].append(dict(stage=stage, layer=layer, resource_class=parts[3] if len(parts) == 4 else None,
                                      feature_count=len(features), summary=summary, source=relative))
        if stage != "standard":
            continue
        if layer in ("generator", "storage"):
            result["metrics"][layer] = dict(capacity_mw=summary.get("value", 0),
                records=summary.get("count", len(features)), drawable=len(features))
            if layer == "generator":
                totals = defaultdict(float)
                for feature in unique_features(features):
                    for category, value in feature["properties"].get("breakdown", {}).items():
                        if value is not None and math.isfinite(value):
                            totals[category] += value
                result["capacity_mix"] = sorted([{**category, "value": totals[category["id"]]}
                    for category in summary.get("categories", [])], key=lambda category: -category["value"])
        elif layer == "network":
            unique = unique_features(features)
            result["metrics"]["network"] = dict(
                buses=sum(f["properties"].get("feature_kind") == "bus" for f in unique),
                branches=sum(f["properties"].get("feature_kind") == "branch" for f in unique), drawable=len(features))
        elif layer == "load":
            if summary.get("unit") != "TWh":
                raise ValueError("Standard load export must declare annual energy in TWh.")
            for feature in unique_features(features):
                box = bounds(positions(feature.get("geometry")))
                if not all(math.isfinite(value) for value in box):
                    continue
                result["provinces"].append(dict(id=feature_id(feature), name=region_name(feature, boundary),
                    value=feature["properties"]["value"], unit="TWh", bounds=box))
            result["provinces"].sort(key=lambda province: -province["value"])
    result["annual_energy_twh"] = sum(province["value"] for province in result["provinces"])
    application = payloads.get("application/uc.json.gz", {})
    times = application.get("time", [])
    result["application"] = dict(summary=application.get("summary", {}), start=times[0] if times else "",
                                 end=times[-1] if times else "", series_count=len(application.get("series", [])))
    return result


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix=".tmp") as temporary:
        temporary_path = Path(temporary.name)
    try:
        with gzip.open(temporary_path, "wt", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def prepare_dashboard(output_root: Path) -> dict[str, Path]:
    """Prepare read-only caches at server startup, never inside an HTTP request."""
    files = sorted(output_root.rglob("*.json.gz"))
    sources = [{"path": file.relative_to(output_root).as_posix(),
                "sha256": hashlib.sha256(file.read_bytes()).hexdigest()} for file in files]
    if not (output_root / "manifest.json.gz").is_file():
        return {}
    revision = hashlib.sha256(json.dumps(sources, sort_keys=True).encode() +
                              Path(__file__).read_bytes()).hexdigest()
    paths = {name: output_root.parent / "cache" / revision / (name + ".json.gz")
             for name in ("overview", "regional-overview")}
    try:
        cached = [read_payload(path) for path in paths.values()]
        if all(item.get("sources") == sources and item.get("schema_version") == SCHEMA_VERSION for item in cached):
            return paths
    except (OSError, ValueError):
        pass
    payloads = {source["path"]: read_payload(output_root / source["path"]) for source in sources}
    overview = dashboard_summary(payloads)
    regional = regional_summary(overview, payloads)
    for name, data in (("overview", overview), ("regional-overview", regional)):
        data["sources"] = sources
        atomic_json(paths[name], data)
    return paths
