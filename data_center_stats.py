#!/usr/bin/env python3
"""Aggregate China's large-scale data-center POIs and draw a pie-map SVG.

The bundled source is the 2024 facility-level dataset published by Yang,
Zhou, and Niu through Science Data Bank (DOI: 10.57760/sciencedb.32970,
version V4). It contains 1,005 POI rows with province, prefecture/city,
coordinates, environmental attributes, and facility type.

The source does not report MW, IT load, rack count, electricity use, or PUE.
Therefore this script uses verified facility count as the scale metric and
does not infer electrical capacity from coordinates, type, or environment.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "data" / "china_datacenter_poi_2024.csv"
DEFAULT_GEOJSON = SCRIPT_DIR / "data" / "china_provinces.geojson"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "outputs"
SOURCE_DOI = "10.57760/sciencedb.32970"
SOURCE_URL = "https://doi.org/10.57760/sciencedb.32970"
SOURCE_VERSION = "V4"
SOURCE_YEAR = 2024

PROVINCE_ALIASES = {
    "anhui": "安徽",
    "beijing": "北京",
    "chongqing": "重庆",
    "fujian": "福建",
    "gansu": "甘肃",
    "guangdong": "广东",
    "guangxi": "广西",
    "guizhou": "贵州",
    "hainan": "海南",
    "hebei": "河北",
    "heilongjiang": "黑龙江",
    "henan": "河南",
    "hubei": "湖北",
    "hunan": "湖南",
    "inner mongolia": "内蒙古",
    "jiangsu": "江苏",
    "jiangxi": "江西",
    "jilin": "吉林",
    "liaoning": "辽宁",
    "ningxia": "宁夏",
    "qinghai": "青海",
    "shaanxi": "陕西",
    "shandong": "山东",
    "shanghai": "上海",
    "shanxi": "山西",
    "sichuan": "四川",
    "tianjin": "天津",
    "xinjiang": "新疆",
    "xizang": "西藏",
    "tibet": "西藏",
    "yunnan": "云南",
    "zhejiang": "浙江",
}

PROVINCE_CENTROIDS = {
    "北京": (116.4, 40.2), "天津": (117.3, 39.3), "河北": (115.3, 38.3),
    "山西": (112.3, 37.8), "内蒙古": (111.6, 41.2), "辽宁": (123.4, 41.6),
    "吉林": (126.2, 43.8), "黑龙江": (128.0, 47.0), "上海": (121.5, 31.2),
    "江苏": (119.5, 32.9), "浙江": (120.1, 29.2), "安徽": (117.2, 31.8),
    "福建": (118.1, 26.1), "江西": (115.7, 27.6), "山东": (118.1, 36.4),
    "河南": (113.5, 33.9), "湖北": (112.3, 30.9), "湖南": (112.6, 27.7),
    "广东": (113.4, 23.4), "广西": (108.8, 23.8), "海南": (109.8, 19.2),
    "重庆": (107.9, 30.1), "四川": (102.7, 30.6), "贵州": (106.7, 26.8),
    "云南": (101.7, 24.7), "西藏": (88.8, 31.7), "陕西": (108.9, 35.2),
    "甘肃": (103.8, 38.0), "青海": (96.0, 35.5), "宁夏": (106.2, 37.3),
    "新疆": (85.0, 41.2),
}

TYPE_ALIASES = {
    "telecom operator type": "telecom_operator",
    "third-party idc service provider type": "third_party_idc",
    "internet enterprise type": "internet_enterprise",
    "enterprise self-use type (non-internet)": "enterprise_self_use",
    "government/state-owned enterprise dedicated type": "government_soe",
    "regional/park supporting type": "regional_park",
    "vertical industry-specific type": "vertical_industry",
    "technology attribute-specific type": "technology_specific",
    "other types": "other",
}

TYPE_ORDER = [
    "telecom_operator",
    "third_party_idc",
    "internet_enterprise",
    "enterprise_self_use",
    "government_soe",
    "regional_park",
    "vertical_industry",
    "technology_specific",
    "other",
]

TYPE_LABELS = {
    "telecom_operator": "telecom operator",
    "third_party_idc": "third-party IDC",
    "internet_enterprise": "internet enterprise",
    "enterprise_self_use": "enterprise self-use",
    "government_soe": "government / SOE",
    "regional_park": "regional / park",
    "vertical_industry": "vertical industry",
    "technology_specific": "technology-specific",
    "other": "other",
}

TYPE_COLORS = {
    "telecom_operator": "#2f7ebc",
    "third_party_idc": "#4fa6a0",
    "internet_enterprise": "#8f63b8",
    "enterprise_self_use": "#d8893a",
    "government_soe": "#6aa84f",
    "regional_park": "#e3b23c",
    "vertical_industry": "#c66b43",
    "technology_specific": "#c84f8f",
    "other": "#9aa7ad",
}


def normalize_text(value: Any) -> str:
    text = "" if pd.isna(value) else str(value)
    return re.sub(r"\s+", " ", text.strip().lower())


def normalize_province(value: Any) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    return PROVINCE_ALIASES.get(text, str(value).strip())


def normalize_city(value: Any) -> str | None:
    text = "" if pd.isna(value) else str(value).strip()
    return re.sub(r"\s+", " ", text) or None


def normalize_type(value: Any) -> str:
    return TYPE_ALIASES.get(normalize_text(value), "other")


def read_source(path: str) -> pd.DataFrame:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Input file not found: {source}")
    raw = pd.read_csv(source, encoding="utf-8-sig")
    required = {
        "Object-ID", "province", "cityname", "longitude", "latitude",
        "temp_C", "precip_mm", "elevation_m", "climate_zone_CN", "Type",
    }
    missing = sorted(required.difference(raw.columns))
    if missing:
        raise SystemExit(f"ScienceDB POI source is missing columns: {', '.join(missing)}")

    work = pd.DataFrame(
        {
            "source_id": raw["Object-ID"].astype(str).str.strip(),
            "province": raw["province"].map(normalize_province),
            "city": raw["cityname"].map(normalize_city),
            "longitude": pd.to_numeric(raw["longitude"], errors="coerce"),
            "latitude": pd.to_numeric(raw["latitude"], errors="coerce"),
            "avg_temp_c": pd.to_numeric(raw["temp_C"], errors="coerce"),
            "avg_precip_mm": pd.to_numeric(raw["precip_mm"], errors="coerce"),
            "avg_elevation_m": pd.to_numeric(raw["elevation_m"], errors="coerce"),
            "climate_zone": raw["climate_zone_CN"].astype(str),
            "facility_type": raw["Type"].map(normalize_type),
        }
    )
    work = work.dropna(subset=["source_id", "province", "city", "longitude", "latitude"])
    # V4 contains one duplicated Object-ID/coordinate assigned to both Liaoning
    # and Jilin. Keeping the first row retains the coordinate-consistent Liaoning record.
    return work.drop_duplicates(subset=["source_id"], keep="first")


def aggregate(source: pd.DataFrame, level: str) -> pd.DataFrame:
    group_cols = ["province", "city"] if level == "city" else ["province"]
    summary = source.groupby(group_cols, as_index=False).agg(
        data_center_count=("source_id", "count"),
        longitude=("longitude", "mean"),
        latitude=("latitude", "mean"),
        avg_temp_c=("avg_temp_c", "mean"),
        avg_precip_mm=("avg_precip_mm", "mean"),
        avg_elevation_m=("avg_elevation_m", "mean"),
        climate_zone_count=("climate_zone", "nunique"),
    )
    counts = (
        source.pivot_table(
            index=group_cols,
            columns="facility_type",
            values="source_id",
            aggfunc="count",
            fill_value=0,
        )
        .reindex(columns=TYPE_ORDER, fill_value=0)
        .reset_index()
        .rename(columns={item: f"{item}_count" for item in TYPE_ORDER})
    )
    output = summary.merge(counts, on=group_cols, how="left")
    type_cols = [f"{item}_count" for item in TYPE_ORDER]
    output["facility_type_count"] = (output[type_cols] > 0).sum(axis=1)
    output["capacity_basis"] = "verified_facility_count"
    output["source_year"] = SOURCE_YEAR
    output["source_version"] = SOURCE_VERSION
    output["source_doi"] = SOURCE_DOI
    return output.sort_values("data_center_count", ascending=False)


def iter_lonlat_points(geometry: dict[str, Any]) -> Iterable[tuple[float, float]]:
    coordinates = geometry.get("coordinates", [])
    if geometry.get("type") == "Polygon":
        polygons = [coordinates]
    elif geometry.get("type") == "MultiPolygon":
        polygons = coordinates
    else:
        polygons = []
    for polygon in polygons:
        for ring in polygon:
            for lon, lat, *_ in ring:
                yield float(lon), float(lat)


def path_from_geometry(geometry: dict[str, Any], project) -> str:
    coordinates = geometry.get("coordinates", [])
    polygons = [coordinates] if geometry.get("type") == "Polygon" else coordinates
    parts: list[str] = []
    for polygon in polygons:
        for ring in polygon:
            if not ring:
                continue
            points = [project(float(point[0]), float(point[1])) for point in ring]
            commands = [f"M {points[0][0]:.2f} {points[0][1]:.2f}"]
            commands.extend(f"L {x:.2f} {y:.2f}" for x, y in points[1:])
            commands.append("Z")
            parts.append(" ".join(commands))
    return " ".join(parts)


def svg_pie(cx: float, cy: float, radius: float, values: list[tuple[str, float]]) -> str:
    total = sum(value for _, value in values)
    if total <= 0:
        return ""
    angle = -math.pi / 2
    pieces: list[str] = []
    for facility_type, value in values:
        if value <= 0:
            continue
        fraction = value / total
        color = TYPE_COLORS[facility_type]
        if fraction >= 0.999:
            pieces.append(
                f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{radius:.2f}" '
                f'fill="{color}" stroke="#ffffff" stroke-width="0.5" />'
            )
            continue
        next_angle = angle + fraction * 2 * math.pi
        x1, y1 = cx + radius * math.cos(angle), cy + radius * math.sin(angle)
        x2, y2 = cx + radius * math.cos(next_angle), cy + radius * math.sin(next_angle)
        large_arc = 1 if fraction > 0.5 else 0
        pieces.append(
            f'<path d="M {cx:.2f} {cy:.2f} L {x1:.2f} {y1:.2f} '
            f'A {radius:.2f} {radius:.2f} 0 {large_arc} 1 {x2:.2f} {y2:.2f} Z" '
            f'fill="{color}" stroke="#ffffff" stroke-width="0.5" />'
        )
        angle = next_angle
    pieces.append(
        f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{radius:.2f}" '
        'fill="none" stroke="#333333" stroke-width="0.7" />'
    )
    return "\n".join(pieces)


def build_svg(
    data: pd.DataFrame,
    geojson: dict[str, Any],
    level: str,
    title: str,
    width: int,
    height: int,
    max_radius: float,
    label_top_n: int,
) -> str:
    points = [
        point
        for feature in geojson.get("features", [])
        for point in iter_lonlat_points(feature.get("geometry", {}))
    ]
    min_lon, max_lon = min(point[0] for point in points), max(point[0] for point in points)
    min_lat, max_lat = min(point[1] for point in points), max(point[1] for point in points)
    pad = 48
    scale = min((width - 2 * pad) / (max_lon - min_lon), (height - 2 * pad) / (max_lat - min_lat))
    map_width, map_height = (max_lon - min_lon) * scale, (max_lat - min_lat) * scale
    x_offset, y_offset = (width - map_width) / 2, (height - map_height) / 2 + 12

    def project(lon: float, lat: float) -> tuple[float, float]:
        return x_offset + (lon - min_lon) * scale, y_offset + (max_lat - lat) * scale

    paths = [
        f'<path d="{path_from_geometry(feature.get("geometry", {}), project)}" '
        'fill="#f7f7f2" stroke="#c8c8bd" stroke-width="0.8" />'
        for feature in geojson.get("features", [])
    ]
    map_data = data.copy()
    if level == "province":
        map_data["longitude"] = map_data["province"].map(lambda value: PROVINCE_CENTROIDS[value][0])
        map_data["latitude"] = map_data["province"].map(lambda value: PROVINCE_CENTROIDS[value][1])
    max_total = max(float(map_data["data_center_count"].max()), 1.0)
    labeled = set(map_data.head(label_top_n).index) if level == "city" else set(map_data.index)
    pies: list[str] = []
    labels: list[str] = []
    for index, row in map_data.iterrows():
        cx, cy = project(float(row["longitude"]), float(row["latitude"]))
        radius = max(
            2.5 if level == "city" else 5.0,
            max_radius * math.sqrt(float(row["data_center_count"]) / max_total),
        )
        values = [
            (item, float(row[f"{item}_count"]))
            for item in TYPE_ORDER
            if float(row[f"{item}_count"]) > 0
        ]
        pies.append(svg_pie(cx, cy, radius, values))
        if index in labeled:
            region = str(row["city"] if level == "city" else row["province"])
            labels.append(
                f'<text x="{cx:.2f}" y="{cy + radius + 8:.2f}" text-anchor="middle" '
                f'font-size="7.5" fill="#333333">{html.escape(region)}</text>'
            )

    legend: list[str] = []
    legend_y = height - 128
    for index, item in enumerate(TYPE_ORDER):
        x = 26 + (index % 5) * 180
        y = legend_y + (index // 5) * 22
        legend.append(
            f'<rect x="{x}" y="{y}" width="12" height="12" fill="{TYPE_COLORS[item]}" />'
            f'<text x="{x + 17}" y="{y + 10}" font-size="10.5" fill="#333333">'
            f'{html.escape(TYPE_LABELS[item])}</text>'
        )
    largest = int(map_data["data_center_count"].max())
    geography = "Prefecture/city" if level == "city" else "Province"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="#ffffff" />
<text x="{width / 2:.0f}" y="28" text-anchor="middle" font-size="20" font-family="Arial, Helvetica, sans-serif" font-weight="700">{html.escape(title)}</text>
<text x="{width / 2:.0f}" y="49" text-anchor="middle" font-size="12" font-family="Arial, Helvetica, sans-serif" fill="#555555">{geography} aggregation | pie: facility type | area: verified facility count (not MW)</text>
<g font-family="Arial, Helvetica, sans-serif">
{chr(10).join(paths)}
{chr(10).join(pies)}
{chr(10).join(labels)}
</g>
<g font-family="Arial, Helvetica, sans-serif">
<text x="26" y="{height - 148}" font-size="12" font-weight="700" fill="#333333">Facility type</text>
{chr(10).join(legend)}
<circle cx="{width - 108}" cy="{height - 86}" r="{max_radius:.1f}" fill="none" stroke="#333333" stroke-width="0.8" />
<text x="{width - 108}" y="{height - 48}" text-anchor="middle" font-size="11" fill="#333333">largest circle: {largest} facilities</text>
<text x="26" y="{height - 15}" font-size="10.5" fill="#555555">Source: Yang, Zhou &amp; Niu, Science Data Bank, {SOURCE_VERSION}, DOI {SOURCE_DOI}. The source has no MW, IT-load, rack, electricity-use, or PUE fields.</text>
</g>
</svg>
"""


def load_config(args: argparse.Namespace) -> argparse.Namespace:
    if args.config:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        for key, value in config.items():
            if not hasattr(args, key):
                raise SystemExit(f"Unknown config key: {key}")
            setattr(args, key, value)
    if not args.table_output:
        args.table_output = str(DEFAULT_OUTPUT_DIR / f"china_data_centers_by_{args.level}.csv")
    if not args.output:
        args.output = str(DEFAULT_OUTPUT_DIR / f"china_data_centers_by_{args.level}_pies.svg")
    if not args.title:
        args.title = f"China large-scale data centers by {args.level} (2024)"
    if args.max_radius is None:
        args.max_radius = 12.0 if args.level == "city" else 30.0
    return args


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aggregate the ScienceDB China data-center POI dataset by province or city."
    )
    parser.add_argument("--config", help="Optional JSON config file.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Local ScienceDB POI CSV.")
    parser.add_argument("--level", choices=["province", "city"], default="province")
    parser.add_argument("--geojson", default=str(DEFAULT_GEOJSON))
    parser.add_argument("--table-output", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--title", default=None)
    parser.add_argument("--width", type=int, default=1120)
    parser.add_argument("--height", type=int, default=850)
    parser.add_argument("--max-radius", type=float, default=None)
    parser.add_argument("--label-top-n", type=int, default=50)
    parser.add_argument("--no-map", action="store_true")
    return load_config(parser.parse_args(argv))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    source = read_source(args.input)
    result = aggregate(source, args.level)
    table_output = Path(args.table_output)
    table_output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(table_output, index=False, encoding="utf-8-sig")
    print(f"Wrote {table_output}")

    if not args.no_map:
        geojson_path = Path(args.geojson)
        if not geojson_path.exists():
            raise SystemExit(f"China province GeoJSON not found: {geojson_path}")
        geojson = json.loads(geojson_path.read_text(encoding="utf-8"))
        svg = build_svg(
            result,
            geojson=geojson,
            level=args.level,
            title=args.title,
            width=args.width,
            height=args.height,
            max_radius=args.max_radius,
            label_top_n=args.label_top_n,
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(svg, encoding="utf-8")
        print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
