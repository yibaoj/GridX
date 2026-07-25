#!/usr/bin/env python3
"""Aggregate GEM power capacity and draw a China pie map.

The script accepts either:
1. GEM China summary tables with province, technology/status, and capacity
   columns; or
2. GEM project/unit-level tracker exports, which are filtered to China and then
   aggregated by province and technology.

One command handles province or prefecture/city aggregation through --level.
It writes a technology-capacity table and a self-contained SVG map.
The SVG renderer intentionally avoids matplotlib/geopandas so the script can
run in lightweight environments.

All inputs are local files. Download GEM data and the map boundary before
running this script.
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "data" / "Global-Integrated-Power-March-2026-II.xlsx"
DEFAULT_GEOJSON = SCRIPT_DIR / "data" / "china_provinces.geojson"
DEFAULT_SHEET = "Power facilities"
DEFAULT_CONFIG = SCRIPT_DIR / "config" / "gem_cap_stats.json"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "outputs"

STATUS_DEFAULT = {"operating"}

STATUS_ALIASES = {
    "operating": "operating",
    "operational": "operating",
    "retired": "retired",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "shelved": "shelved",
    "mothballed": "mothballed",
    "announced": "announced",
    "pre-construction": "pre-construction",
    "preconstruction": "pre-construction",
    "construction": "construction",
    "in construction": "construction",
    "under construction": "construction",
    "prospective": "prospective",
}

TECH_COLORS = {
    "thermal": "#6b5b53",
    "coal": "#5b5651",
    "oil/gas": "#d8893a",
    "gas": "#d8893a",
    "oil": "#8a5a44",
    "nuclear": "#8f63b8",
    "hydro": "#2f7ebc",
    "wind": "#4fa6a0",
    "solar": "#e3b23c",
    "bioenergy": "#6aa84f",
    "geothermal": "#c66b43",
    "storage": "#7f8c8d",
    "other": "#b7b7b7",
}

TECH_ORDER = [
    "coal",
    "oil/gas",
    "gas",
    "oil",
    "nuclear",
    "hydro",
    "wind",
    "solar",
    "bioenergy",
    "geothermal",
    "storage",
    "other",
]

NEA_TECH_ORDER = ["thermal", "hydro", "nuclear", "wind", "solar", "storage", "other"]

PROVINCE_ALIASES = {
    "anhui": "安徽",
    "beijing": "北京",
    "chongqing": "重庆",
    "fujian": "福建",
    "gansu": "甘肃",
    "guangdong": "广东",
    "guangxi": "广西",
    "guangxi zhuang": "广西",
    "guangxi zhuang autonomous region": "广西",
    "guizhou": "贵州",
    "hainan": "海南",
    "hebei": "河北",
    "heilongjiang": "黑龙江",
    "henan": "河南",
    "hubei": "湖北",
    "hunan": "湖南",
    "inner mongolia": "内蒙古",
    "inner mongolia autonomous region": "内蒙古",
    "jiangsu": "江苏",
    "jiangxi": "江西",
    "jilin": "吉林",
    "liaoning": "辽宁",
    "ningxia": "宁夏",
    "ningxia hui": "宁夏",
    "ningxia hui autonomous region": "宁夏",
    "qinghai": "青海",
    "shaanxi": "陕西",
    "shandong": "山东",
    "shanghai": "上海",
    "shanxi": "山西",
    "sichuan": "四川",
    "tianjin": "天津",
    "tibet": "西藏",
    "tibet autonomous region": "西藏",
    "xinjiang": "新疆",
    "xinjiang uygur": "新疆",
    "xinjiang uygur autonomous region": "新疆",
    "yunnan": "云南",
    "zhejiang": "浙江",
    "hong kong": "香港",
    "macao": "澳门",
    "macau": "澳门",
    "taiwan": "台湾",
}

# Approximate provincial centroids, used to place pie charts.
PROVINCE_CENTROIDS = {
    "北京": (116.4, 40.2),
    "天津": (117.3, 39.3),
    "河北": (115.3, 38.3),
    "山西": (112.3, 37.8),
    "内蒙古": (111.6, 41.2),
    "辽宁": (123.4, 41.6),
    "吉林": (126.2, 43.8),
    "黑龙江": (128.0, 47.0),
    "上海": (121.5, 31.2),
    "江苏": (119.5, 32.9),
    "浙江": (120.1, 29.2),
    "安徽": (117.2, 31.8),
    "福建": (118.1, 26.1),
    "江西": (115.7, 27.6),
    "山东": (118.1, 36.4),
    "河南": (113.5, 33.9),
    "湖北": (112.3, 30.9),
    "湖南": (112.6, 27.7),
    "广东": (113.4, 23.4),
    "广西": (108.8, 23.8),
    "海南": (109.8, 19.2),
    "重庆": (107.9, 30.1),
    "四川": (102.7, 30.6),
    "贵州": (106.7, 26.8),
    "云南": (101.7, 24.7),
    "西藏": (88.8, 31.7),
    "陕西": (108.9, 35.2),
    "甘肃": (103.8, 38.0),
    "青海": (96.0, 35.5),
    "宁夏": (106.2, 37.3),
    "新疆": (85.0, 41.2),
    "台湾": (121.0, 23.7),
    "香港": (114.2, 22.3),
    "澳门": (113.6, 22.2),
}


def normalize_text(value: Any) -> str:
    text = "" if pd.isna(value) else str(value)
    text = text.strip().lower()
    text = text.replace("\ufeff", "")
    text = re.sub(r"\s+", " ", text)
    return text


def compact_key(value: Any) -> str:
    text = normalize_text(value)
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text)


def read_table(source: str, sheet_name: str | None = None) -> pd.DataFrame:
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    raw = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        excel = pd.ExcelFile(io.BytesIO(raw))
        sheet = sheet_name if sheet_name in excel.sheet_names else excel.sheet_names[0]
        return pd.read_excel(excel, sheet_name=sheet)

    text = raw.decode("utf-8-sig", errors="replace")
    return read_csv_with_header_detection(text)


def read_csv_with_header_detection(text: str) -> pd.DataFrame:
    rows = list(csv.reader(io.StringIO(text)))
    best_idx = 0
    best_score = -1
    clues = {
        "province",
        "subnational",
        "technology",
        "fuel",
        "status",
        "capacity",
        "mw",
        "country",
        "unit",
    }
    for idx, row in enumerate(rows[:20]):
        joined = " ".join(normalize_text(cell) for cell in row)
        score = sum(clue in joined for clue in clues) + sum(bool(cell.strip()) for cell in row)
        if score > best_score:
            best_idx = idx
            best_score = score
    return pd.read_csv(io.StringIO(text), header=best_idx)


def find_column(columns: Iterable[str], candidates: list[str]) -> str | None:
    normalized = {col: compact_key(col) for col in columns}
    for candidate in candidates:
        key = compact_key(candidate)
        for col, norm in normalized.items():
            if norm == key:
                return col
    for candidate in candidates:
        key = compact_key(candidate)
        for col, norm in normalized.items():
            if key in norm:
                return col
    return None


def clean_capacity(value: Any) -> float:
    if pd.isna(value):
        return 0.0
    text = str(value).replace(",", "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else 0.0


def normalize_province(value: Any) -> str | None:
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    text = re.sub(r"(省|市|自治区|特别行政区|壮族|回族|维吾尔)", "", text)
    if text in PROVINCE_CENTROIDS:
        return text
    return PROVINCE_ALIASES.get(normalize_text(value))


def normalize_city(value: Any) -> str | None:
    text = "" if pd.isna(value) else str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return re.sub(r"\s+", " ", text)


def normalize_status(value: Any) -> str:
    text = normalize_text(value)
    if text in STATUS_ALIASES:
        return STATUS_ALIASES[text]
    for key, normalized in STATUS_ALIASES.items():
        if key in text:
            return normalized
    return text


def normalize_technology(value: Any) -> str:
    text = normalize_text(value)
    if text == "thermal" or text.startswith("thermal ") or "火电" in text:
        return "thermal"
    if "oil/gas" in text or "oil and gas" in text:
        return "oil/gas"
    if "utility-scale solar" in text:
        return "solar"
    if "hydropower" in text:
        return "hydro"
    if any(term in text for term in ["coal", "煤"]):
        return "coal"
    if any(term in text for term in ["gas", "lng", "天然气", "燃气"]):
        return "gas"
    if any(term in text for term in ["oil", "diesel", "石油", "燃油"]):
        return "oil"
    if any(term in text for term in ["nuclear", "核"]):
        return "nuclear"
    if any(term in text for term in ["hydro", "pumped", "水电", "抽水蓄能"]):
        return "hydro"
    if any(term in text for term in ["wind", "风"]):
        return "wind"
    if any(term in text for term in ["solar", "pv", "photovoltaic", "光伏", "太阳"]):
        return "solar"
    if any(term in text for term in ["bio", "biomass", "生物质"]):
        return "bioenergy"
    if any(term in text for term in ["geothermal", "地热"]):
        return "geothermal"
    if any(term in text for term in ["battery", "storage", "储能"]):
        return "storage"
    return "other"


def aggregate_capacity_rows(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    work = df.copy()
    has_location = {"longitude", "latitude"}.issubset(work.columns)
    if has_location:
        located = work["longitude"].notna() & work["latitude"].notna()
        work["located_capacity_mw"] = work["capacity_mw"].where(located, 0.0)
        work["weighted_longitude"] = work["longitude"].fillna(0.0) * work["located_capacity_mw"]
        work["weighted_latitude"] = work["latitude"].fillna(0.0) * work["located_capacity_mw"]
        grouped = work.groupby(group_cols, as_index=False).agg(
            capacity_mw=("capacity_mw", "sum"),
            located_capacity_mw=("located_capacity_mw", "sum"),
            weighted_longitude=("weighted_longitude", "sum"),
            weighted_latitude=("weighted_latitude", "sum"),
        )
        denominator = grouped["located_capacity_mw"].replace(0, pd.NA)
        grouped["longitude"] = grouped["weighted_longitude"] / denominator
        grouped["latitude"] = grouped["weighted_latitude"] / denominator
        return grouped.drop(columns=["weighted_longitude", "weighted_latitude"])
    return work.groupby(group_cols, as_index=False)["capacity_mw"].sum()


def apply_technology_scheme(df: pd.DataFrame, scheme: str) -> pd.DataFrame:
    if scheme == "gem":
        return df
    work = df.copy()
    work["technology"] = work["technology"].replace(
        {
            "coal": "thermal",
            "oil/gas": "thermal",
            "gas": "thermal",
            "oil": "thermal",
            "bioenergy": "thermal",
            "geothermal": "other",
        }
    )
    group_cols = [col for col in ["province", "city", "technology"] if col in work.columns]
    return aggregate_capacity_rows(work, group_cols)


def filter_status(df: pd.DataFrame, status_col: str | None, statuses: set[str]) -> pd.DataFrame:
    if not statuses or status_col is None:
        return df.copy()
    status_series = df[status_col].map(normalize_status)
    return df.loc[status_series.isin(statuses)].copy()


def parse_long_table(
    df: pd.DataFrame,
    statuses: set[str],
    country_scope: str,
    start_year_max: int | None,
    exclude_captive: bool,
    level: str,
) -> pd.DataFrame | None:
    province_col = find_column(
        df.columns,
        [
            "province",
            "province/area",
            "chinese province",
            "subnational unit (state, province)",
            "subnational unit",
            "subnational",
            "state/province",
            "state",
        ],
    )
    tech_col = find_column(
        df.columns,
        ["type", "technology", "fuel", "fuel type", "energy source", "power source"],
    )
    fallback_tech_cols = [
        col
        for col in [
            find_column(df.columns, ["technology"]),
            find_column(df.columns, ["fuel (combustion only)", "fuel"]),
        ]
        if col and col != tech_col
    ]
    capacity_col = find_column(
        df.columns,
        ["capacity (mw)", "capacity mw", "capacity", "unit capacity (mw)", "mw"],
    )
    status_col = find_column(df.columns, ["status", "project status", "unit status"])
    country_col = find_column(df.columns, ["country/area", "country", "area"])
    city_col = find_column(
        df.columns,
        ["major area (prefecture, district)", "prefecture", "city", "major area"],
    )
    start_year_col = find_column(df.columns, ["start year", "year online", "online year"])
    captive_col = find_column(df.columns, ["captive industry type", "captive industry use"])
    unit_id_col = find_column(df.columns, ["gem unit/phase id", "unit/phase id", "unit id"])
    latitude_col = find_column(df.columns, ["latitude", "lat"])
    longitude_col = find_column(df.columns, ["longitude", "lon", "lng"])

    if not province_col or not tech_col or not capacity_col:
        return None

    work = df.copy()
    if country_col:
        country = work[country_col].map(normalize_text)
        if country_scope == "mainland":
            work = work[country.eq("china") | country.eq("中国")]
        else:
            work = work[country.str.contains("china|中国|hong kong|macao|macau|taiwan", na=False)]
    work = filter_status(work, status_col, statuses)
    if start_year_max is not None and start_year_col:
        start_year = pd.to_numeric(work[start_year_col], errors="coerce")
        work = work[start_year.isna() | (start_year <= start_year_max)]
    if exclude_captive and captive_col:
        work = work[work[captive_col].isna() | work[captive_col].astype(str).str.strip().eq("")]
    if unit_id_col:
        unit_ids = work[unit_id_col].astype(str).str.strip()
        identified = work[unit_ids.ne("") & unit_ids.ne("nan")].drop_duplicates(unit_id_col)
        unidentified = work[unit_ids.eq("") | unit_ids.eq("nan")]
        work = pd.concat([identified, unidentified], ignore_index=True)

    tech_values = work[tech_col].copy()
    for fallback_col in fallback_tech_cols:
        missing = tech_values.isna() | tech_values.astype(str).str.strip().isin(["", "nan"])
        tech_values.loc[missing] = work.loc[missing, fallback_col]

    out = pd.DataFrame(
        {
            "province": work[province_col].map(normalize_province),
            "technology": tech_values.map(normalize_technology),
            "capacity_mw": work[capacity_col].map(clean_capacity),
            "longitude": pd.to_numeric(work[longitude_col], errors="coerce") if longitude_col else pd.NA,
            "latitude": pd.to_numeric(work[latitude_col], errors="coerce") if latitude_col else pd.NA,
        }
    )
    if city_col:
        out["city"] = work[city_col].map(normalize_city)
    else:
        out["city"] = None
    out = out.dropna(subset=["province"])
    if level == "city":
        out = out.dropna(subset=["city"])
    out = out[out["capacity_mw"] > 0]
    if out.empty:
        return None
    group_cols = ["province", "technology"]
    if level == "city":
        group_cols = ["province", "city", "technology"]
    return aggregate_capacity_rows(out, group_cols)


def parse_wide_summary(df: pd.DataFrame, statuses: set[str]) -> pd.DataFrame | None:
    province_col = find_column(
        df.columns,
        ["province", "province/area", "chinese province", "subnational unit", "region"],
    )
    if not province_col:
        # Many summary tables use the first non-empty column for geography.
        province_col = df.columns[0]

    rows: list[dict[str, Any]] = []
    status_terms = set(STATUS_ALIASES)
    for col in df.columns:
        if col == province_col:
            continue
        tech = normalize_technology(col)
        status = normalize_status(col)
        col_text = normalize_text(col)
        if tech == "other":
            continue
        if statuses and any(term in col_text for term in status_terms):
            if status not in statuses:
                continue
        for _, record in df[[province_col, col]].iterrows():
            province = normalize_province(record[province_col])
            capacity = clean_capacity(record[col])
            if province and capacity > 0:
                rows.append(
                    {
                        "province": province,
                        "technology": tech,
                        "capacity_mw": capacity,
                    }
                )
    if not rows:
        return None
    out = pd.DataFrame(rows)
    return out.groupby(["province", "technology"], as_index=False)["capacity_mw"].sum()


def load_capacity_data(args: argparse.Namespace) -> pd.DataFrame:
    source = args.input
    errors: list[str] = []
    statuses = set() if args.all_statuses else {normalize_status(s) for s in args.status}

    try:
        raw = read_table(source, sheet_name=args.sheet)
        long = parse_long_table(
            raw,
            statuses=statuses,
            country_scope=args.country_scope,
            start_year_max=args.start_year_max,
            exclude_captive=args.exclude_captive,
            level=args.level,
        )
        if long is not None and not long.empty:
            return apply_technology_scheme(long, args.technology_scheme)
        wide = parse_wide_summary(raw, statuses=statuses) if args.level == "province" else None
        if wide is not None and not wide.empty:
            return apply_technology_scheme(wide, args.technology_scheme)
        errors.append(f"{source}: no recognizable GEM province/capacity columns")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{source}: {exc}")

    detail = "\n".join(f"- {msg}" for msg in errors)
    raise SystemExit(
        "Could not load GEM capacity data. Download the GEM China summary table "
        f"or full GEM xlsx and save it locally, for example at {DEFAULT_INPUT}.\n" + detail
    )


def iter_lonlat_points(geometry: dict[str, Any]) -> Iterable[tuple[float, float]]:
    coords = geometry.get("coordinates", [])
    geom_type = geometry.get("type")
    if geom_type == "Polygon":
        for ring in coords:
            for lon, lat, *_ in ring:
                yield float(lon), float(lat)
    elif geom_type == "MultiPolygon":
        for polygon in coords:
            for ring in polygon:
                for lon, lat, *_ in ring:
                    yield float(lon), float(lat)


def path_from_geometry(
    geometry: dict[str, Any],
    project,
) -> str:
    parts: list[str] = []
    geom_type = geometry.get("type")
    polygons = geometry.get("coordinates", [])
    if geom_type == "Polygon":
        polygons = [polygons]
    for polygon in polygons:
        for ring in polygon:
            if not ring:
                continue
            projected = [project(float(p[0]), float(p[1])) for p in ring]
            start = projected[0]
            d = [f"M {start[0]:.2f} {start[1]:.2f}"]
            d.extend(f"L {x:.2f} {y:.2f}" for x, y in projected[1:])
            d.append("Z")
            parts.append(" ".join(d))
    return " ".join(parts)


def svg_pie(cx: float, cy: float, r: float, values: list[tuple[str, float]]) -> str:
    total = sum(value for _, value in values)
    if total <= 0:
        return ""
    output: list[str] = []
    angle = -math.pi / 2
    for tech, value in values:
        if value <= 0:
            continue
        frac = value / total
        color = TECH_COLORS.get(tech, TECH_COLORS["other"])
        if frac >= 0.999:
            output.append(
                f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" '
                f'fill="{color}" stroke="#ffffff" stroke-width="0.5" />'
            )
            continue
        next_angle = angle + frac * 2 * math.pi
        x1 = cx + r * math.cos(angle)
        y1 = cy + r * math.sin(angle)
        x2 = cx + r * math.cos(next_angle)
        y2 = cy + r * math.sin(next_angle)
        large_arc = 1 if frac > 0.5 else 0
        output.append(
            f'<path d="M {cx:.2f} {cy:.2f} L {x1:.2f} {y1:.2f} '
            f'A {r:.2f} {r:.2f} 0 {large_arc} 1 {x2:.2f} {y2:.2f} Z" '
            f'fill="{color}" stroke="#ffffff" stroke-width="0.5" />'
        )
        angle = next_angle
    output.append(
        f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" '
        'fill="none" stroke="#333333" stroke-width="0.7" />'
    )
    return "\n".join(output)


def build_svg(
    aggregated: pd.DataFrame,
    geojson: dict[str, Any],
    level: str,
    title: str,
    width: int,
    height: int,
    max_radius: float,
    label_top_n: int,
) -> str:
    all_points: list[tuple[float, float]] = []
    for feature in geojson.get("features", []):
        all_points.extend(iter_lonlat_points(feature.get("geometry", {})))
    if not all_points:
        all_points = list(PROVINCE_CENTROIDS.values())

    min_lon = min(p[0] for p in all_points)
    max_lon = max(p[0] for p in all_points)
    min_lat = min(p[1] for p in all_points)
    max_lat = max(p[1] for p in all_points)
    pad = 48
    scale = min((width - 2 * pad) / (max_lon - min_lon), (height - 2 * pad) / (max_lat - min_lat))
    map_w = (max_lon - min_lon) * scale
    map_h = (max_lat - min_lat) * scale
    x_offset = (width - map_w) / 2
    y_offset = (height - map_h) / 2 + 12

    def project(lon: float, lat: float) -> tuple[float, float]:
        x = x_offset + (lon - min_lon) * scale
        y = y_offset + (max_lat - lat) * scale
        return x, y

    paths = []
    for feature in geojson.get("features", []):
        d = path_from_geometry(feature.get("geometry", {}), project)
        if d:
            paths.append(f'<path d="{d}" fill="#f7f7f2" stroke="#c8c8bd" stroke-width="0.8" />')

    tech_order = NEA_TECH_ORDER if "thermal" in set(aggregated["technology"]) else TECH_ORDER
    group_cols = region_columns(level)
    regions = aggregate_capacity_rows(aggregated, group_cols)
    regions = regions.rename(columns={"capacity_mw": "total_mw"}).sort_values("total_mw", ascending=False)
    if level == "province":
        regions["longitude"] = regions["province"].map(lambda value: PROVINCE_CENTROIDS.get(value, (pd.NA, pd.NA))[0])
        regions["latitude"] = regions["province"].map(lambda value: PROVINCE_CENTROIDS.get(value, (pd.NA, pd.NA))[1])
    regions = regions.dropna(subset=["longitude", "latitude"])
    max_total = float(regions["total_mw"].max()) if not regions.empty else 1.0
    labeled = set(regions.head(label_top_n).index) if level == "city" else set(regions.index)
    pies: list[str] = []
    labels: list[str] = []
    for index, row in regions.iterrows():
        cx, cy = project(float(row["longitude"]), float(row["latitude"]))
        total = float(row["total_mw"])
        radius = max(2.5 if level == "city" else 5.0, max_radius * math.sqrt(total / max_total))
        mask = aggregated["province"].eq(row["province"])
        if level == "city":
            mask &= aggregated["city"].eq(row["city"])
        slices = (
            aggregated.loc[mask]
            .set_index("technology")["capacity_mw"]
            .reindex(tech_order)
            .dropna()
        )
        values = [(tech, float(value)) for tech, value in slices.items() if value > 0]
        region_name = str(row["city"] if level == "city" else row["province"])
        pies.append(
            f'<g class="pie" data-region="{html.escape(region_name)}">\n'
            f'{svg_pie(cx, cy, radius, values)}\n</g>'
        )
        if index in labeled:
            labels.append(
                f'<text x="{cx:.2f}" y="{cy + radius + 8:.2f}" text-anchor="middle" '
                f'font-size="7.5" fill="#333333">{html.escape(region_name)}</text>'
            )

    legend_items: list[str] = []
    lx, ly = 32, height - 128
    for idx, tech in enumerate(tech_order):
        x = lx + (idx % 6) * 92
        y = ly + (idx // 6) * 22
        legend_items.append(
            f'<rect x="{x}" y="{y}" width="12" height="12" fill="{TECH_COLORS[tech]}" />'
            f'<text x="{x + 17}" y="{y + 10}" font-size="11" fill="#333333">{tech}</text>'
        )

    max_label = f"{max_total / 1000:.1f} GW" if max_total >= 1000 else f"{max_total:.0f} MW"
    geography = "Prefecture/city" if level == "city" else "Province"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="#ffffff" />
<text x="{width / 2:.0f}" y="28" text-anchor="middle" font-size="20" font-family="Arial, Helvetica, sans-serif" font-weight="700">{html.escape(title)}</text>
<text x="{width / 2:.0f}" y="49" text-anchor="middle" font-size="12" font-family="Arial, Helvetica, sans-serif" fill="#555555">{geography} aggregation | pie: technology mix | area: operating capacity | source: GEM</text>
<g font-family="Arial, Helvetica, sans-serif">
{chr(10).join(paths)}
{chr(10).join(pies)}
{chr(10).join(labels)}
</g>
<g font-family="Arial, Helvetica, sans-serif">
<text x="32" y="{height - 148}" font-size="12" font-weight="700" fill="#333333">Technology</text>
{chr(10).join(legend_items)}
<circle cx="{width - 116}" cy="{height - 86}" r="{max_radius:.1f}" fill="none" stroke="#333333" stroke-width="0.8" />
<text x="{width - 116}" y="{height - 48}" text-anchor="middle" font-size="11" fill="#333333">largest circle: {max_label}</text>
</g>
</svg>
"""


def region_columns(level: str) -> list[str]:
    return ["province", "city"] if level == "city" else ["province"]


def make_region_label(df: pd.DataFrame, level: str) -> pd.Series:
    if level == "city":
        return df["province"].astype(str) + "-" + df["city"].astype(str)
    return df["province"].astype(str)


def pivot_capacity(aggregated: pd.DataFrame, level: str) -> pd.DataFrame:
    tech_order = NEA_TECH_ORDER if "thermal" in set(aggregated["technology"]) else TECH_ORDER
    index_cols = region_columns(level)
    pivot = (
        aggregated.pivot_table(
            index=index_cols,
            columns="technology",
            values="capacity_mw",
            aggfunc="sum",
            fill_value=0,
        )
        .reindex(columns=tech_order, fill_value=0)
        .reset_index()
    )
    pivot["total_mw"] = pivot[tech_order].sum(axis=1)
    if level == "city":
        locations = aggregate_capacity_rows(aggregated, index_cols)[
            [*index_cols, "located_capacity_mw", "longitude", "latitude"]
        ]
        pivot = pivot.merge(locations, on=index_cols, how="left")
    return pivot.sort_values("total_mw", ascending=False)


def capacity_factor(unit: str) -> float:
    unit = normalize_text(unit)
    if unit in {"mw", "兆瓦"}:
        return 1.0
    if unit in {"gw", "吉瓦"}:
        return 1000.0
    if unit in {"万千瓦", "10mw"}:
        return 10.0
    raise SystemExit(f"Unsupported official unit: {unit}")


def load_official_stats(path: str, args: argparse.Namespace) -> pd.DataFrame:
    raw = read_table(path, sheet_name=args.official_sheet)
    province_col = find_column(raw.columns, ["province", "省份", "地区", "region"])
    city_col = find_column(raw.columns, ["city", "地市", "城市", "prefecture"])
    tech_col = find_column(raw.columns, ["technology", "type", "电源类型", "能源类型"])
    capacity_col = find_column(raw.columns, ["capacity_mw", "capacity", "装机容量", "装机"])
    factor = capacity_factor(args.official_unit)

    rows: list[dict[str, Any]] = []
    if tech_col and capacity_col:
        for _, record in raw.iterrows():
            province = normalize_province(record[province_col]) if province_col else normalize_city(record.get("region"))
            city = normalize_city(record[city_col]) if city_col else None
            if not province:
                continue
            if args.level == "city" and not city:
                continue
            rows.append(
                {
                    "province": province,
                    "city": city,
                    "technology": normalize_technology(record[tech_col]),
                    "official_capacity_mw": clean_capacity(record[capacity_col]) * factor,
                }
            )
    else:
        id_cols = {col for col in [province_col, city_col] if col}
        for col in raw.columns:
            if col in id_cols:
                continue
            tech = normalize_technology(col)
            if tech == "other":
                continue
            for _, record in raw.iterrows():
                province = normalize_province(record[province_col]) if province_col else None
                city = normalize_city(record[city_col]) if city_col else None
                capacity = clean_capacity(record[col]) * factor
                if not province or capacity <= 0:
                    continue
                if args.level == "city" and not city:
                    continue
                rows.append(
                    {
                        "province": province,
                        "city": city,
                        "technology": tech,
                        "official_capacity_mw": capacity,
                    }
                )

    official = pd.DataFrame(rows)
    if official.empty:
        raise SystemExit("No recognizable official statistics rows. Check columns and --official-unit.")
    if args.technology_scheme == "nea":
        official["technology"] = official["technology"].replace(
            {
                "coal": "thermal",
                "oil/gas": "thermal",
                "gas": "thermal",
                "oil": "thermal",
                "bioenergy": "thermal",
            }
        )
    group_cols = region_columns(args.level) + ["technology"]
    return official.groupby(group_cols, as_index=False)["official_capacity_mw"].sum()


def write_comparison(
    args: argparse.Namespace,
    aggregated: pd.DataFrame,
) -> None:
    if not args.official_stats:
        return
    official = load_official_stats(args.official_stats, args)
    group_cols = region_columns(args.level) + ["technology"]
    gem = aggregated.rename(columns={"capacity_mw": "gem_capacity_mw"})
    comparison = official.merge(gem, on=group_cols, how="outer")
    comparison["official_capacity_mw"] = comparison["official_capacity_mw"].fillna(0)
    comparison["gem_capacity_mw"] = comparison["gem_capacity_mw"].fillna(0)
    comparison["difference_mw"] = comparison["gem_capacity_mw"] - comparison["official_capacity_mw"]
    comparison["difference_pct"] = comparison["difference_mw"] / comparison["official_capacity_mw"].replace(0, pd.NA)
    comparison = comparison.sort_values(group_cols)
    output = Path(args.comparison_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    comparison.to_csv(output, index=False, encoding="utf-8-sig")


def write_outputs(args: argparse.Namespace, aggregated: pd.DataFrame) -> None:
    output_csv = Path(args.table_output)
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    pivot = pivot_capacity(aggregated, args.level)
    pivot.to_csv(output_csv, index=False, encoding="utf-8-sig")
    write_comparison(args, aggregated)

    if args.no_map:
        return

    output_svg = Path(args.output)
    output_svg.parent.mkdir(parents=True, exist_ok=True)

    geojson_path = Path(args.geojson)
    if not geojson_path.exists():
        raise SystemExit(f"China province GeoJSON not found: {geojson_path}")
    geojson = json.loads(geojson_path.read_text(encoding="utf-8"))

    svg = build_svg(
        aggregated,
        geojson=geojson,
        level=args.level,
        title=args.title,
        width=args.width,
        height=args.height,
        max_radius=args.max_radius,
        label_top_n=args.label_top_n,
    )
    output_svg.write_text(svg, encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aggregate GEM China power capacity by province or city and draw a pie-map SVG."
    )
    parser.add_argument(
        "--config",
        default=None,
        help=f"Optional JSON config file. Example default location: {DEFAULT_CONFIG}.",
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help="Local GEM CSV/XLSX. Default: paper-codes/data/Global-Integrated-Power-March-2026-II.xlsx.",
    )
    parser.add_argument(
        "--sheet",
        default=DEFAULT_SHEET,
        help="Excel sheet to read when --input is xlsx. Default: Power facilities.",
    )
    parser.add_argument(
        "--status",
        nargs="+",
        default=sorted(STATUS_DEFAULT),
        help="GEM status values to include. Default: operating.",
    )
    parser.add_argument(
        "--all-statuses",
        action="store_true",
        help="Include all project/status categories instead of filtering to --status.",
    )
    parser.add_argument(
        "--country-scope",
        choices=["mainland", "all"],
        default="mainland",
        help="Use China mainland only or China plus Hong Kong/Macao/Taiwan. Default: mainland.",
    )
    parser.add_argument(
        "--start-year-max",
        type=int,
        help="Keep units with missing start year or Start year <= this value.",
    )
    parser.add_argument(
        "--exclude-captive",
        action="store_true",
        help="Exclude GEM rows with a non-empty Captive Industry Type/Use field.",
    )
    parser.add_argument(
        "--level",
        choices=["province", "city"],
        default="province",
        help="Aggregation and pie-map level. Default: province.",
    )
    parser.add_argument(
        "--technology-scheme",
        choices=["gem", "nea"],
        default="gem",
        help="Technology grouping: GEM detailed types or NEA-style thermal/hydro/nuclear/wind/solar. Default: gem.",
    )
    parser.add_argument(
        "--official-stats",
        help="Optional local CSV/XLSX official statistics table for comparison.",
    )
    parser.add_argument(
        "--official-sheet",
        default=None,
        help="Excel sheet to read when --official-stats is xlsx.",
    )
    parser.add_argument(
        "--official-unit",
        default="mw",
        help="Unit in official statistics: mw, gw, or 万千瓦. Default: mw.",
    )
    parser.add_argument(
        "--comparison-output",
        default=str(DEFAULT_OUTPUT_DIR / "china_capacity_official_comparison.csv"),
        help="Output CSV path for GEM versus official statistics comparison.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output SVG path.",
    )
    parser.add_argument(
        "--table-output",
        default=None,
        help="Output aggregated CSV path.",
    )
    parser.add_argument(
        "--geojson",
        default=str(DEFAULT_GEOJSON),
        help="Local China province GeoJSON. Default: paper-codes/data/china_provinces.geojson.",
    )
    parser.add_argument("--width", type=int, default=1120)
    parser.add_argument("--height", type=int, default=850)
    parser.add_argument("--max-radius", type=float, default=None)
    parser.add_argument(
        "--label-top-n",
        type=int,
        default=50,
        help="In city mode, label only the largest N regions. All pies are still drawn.",
    )
    parser.add_argument(
        "--title",
        default=None,
    )
    parser.add_argument(
        "--no-map",
        action="store_true",
        help="Skip SVG map output.",
    )
    args = parser.parse_args(argv)
    if args.config:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        for key, value in config.items():
            if not hasattr(args, key):
                raise SystemExit(f"Unknown config key: {key}")
            setattr(args, key, value)
    if not args.table_output:
        args.table_output = str(DEFAULT_OUTPUT_DIR / f"china_power_capacity_by_{args.level}.csv")
    if not args.output:
        args.output = str(DEFAULT_OUTPUT_DIR / f"china_power_capacity_by_{args.level}_pies.svg")
    if not args.title:
        args.title = f"China operating power capacity by {args.level} (GEM)"
    if args.max_radius is None:
        args.max_radius = 12.0 if args.level == "city" else 30.0
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    aggregated = load_capacity_data(args)
    if aggregated.empty:
        raise SystemExit("No capacity data after filtering.")
    write_outputs(args, aggregated)
    print(f"Wrote {args.table_output}")
    if not args.no_map:
        print(f"Wrote {args.output}")
    if args.official_stats:
        print(f"Wrote {args.comparison_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
