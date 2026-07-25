import argparse
import re
from pathlib import Path

import geopandas as gpd
import pandas as pd


def maximum_voltage(value):
    if pd.isna(value):
        return None

    numbers = [
        float(number)
        for number in re.findall(r"\d+(?:\.\d+)?", str(value))
    ]

    # OSM规范要求电压单位为V；排除回路数等小数字。
    voltages = [number for number in numbers if number >= 1000]
    return max(voltages, default=None)


parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--min-kv", type=float, default=220)
parser.add_argument("--output-dir", default="outputs")
args = parser.parse_args()

minimum_voltage = args.min_kv * 1000
output_dir = Path(args.output_dir)
output_dir.mkdir(parents=True, exist_ok=True)

gdf = gpd.read_file(args.input)

if "power" not in gdf.columns or "voltage" not in gdf.columns:
    raise ValueError("Input must contain power and voltage fields")

gdf["voltage_max_v"] = (
    gdf["voltage"].map(maximum_voltage).astype("Float64")
)

line_object = (
    gdf["power"].isin(["line", "cable"])
    & gdf.geometry.geom_type.isin(["LineString", "MultiLineString"])
)
station_object = gdf["power"].isin(["substation", "converter"])
above_threshold = gdf["voltage_max_v"].ge(minimum_voltage).fillna(False)

lines = gdf[line_object & above_threshold].copy()
stations = gdf[station_object & above_threshold].copy()
unknown_lines = gdf[line_object & gdf["voltage_max_v"].isna()].copy()

threshold_name = f"{args.min_kv:g}kv"

lines.to_file(
    output_dir / f"china-power-{threshold_name}-lines.geojson",
    driver="GeoJSON",
)
stations.to_file(
    output_dir / f"china-power-{threshold_name}-substations.geojson",
    driver="GeoJSON",
)
unknown_lines.to_file(
    output_dir / "china-power-unknown-voltage-lines.geojson",
    driver="GeoJSON",
)

print(f"Lines: {len(lines):,}")
print(f"Substations: {len(stations):,}")
print(f"Unknown-voltage lines: {len(unknown_lines):,}")