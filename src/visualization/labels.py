"""Bilingual display text shared by static plots."""

from __future__ import annotations


TEXT = {
    "class": {"zh": "类别", "en": "Class"},
    "total_capacity": {"zh": "总容量", "en": "Total capacity"},
    "junction": {"zh": "线路连接点", "en": "Junction"},
    "station": {"zh": "变电站", "en": "Station"},
    "standard_spatial": {
        "zh": "标准空间单元（{count:,}）",
        "en": "Standard spatial units ({count:,})",
    },
    "mapped_spatial": {
        "zh": "映射空间单元（{count:,}）",
        "en": "Mapped spatial cells ({count:,})",
    },
    "standard_network": {
        "zh": "标准电网：{buses:,}个母线，{branches:,}条支路",
        "en": "Standard network: {buses:,} buses, {branches:,} branches",
    },
    "mapped_network": {
        "zh": "映射后最大连通电网：{buses:,}个母线，{branches:,}条支路",
        "en": "Mapped largest connected network: {buses:,} buses, {branches:,} branches",
    },
    "case_network": {
        "zh": "算例电网：{buses:,}个母线，{branches:,}条支路",
        "en": "Case network: {buses:,} buses, {branches:,} branches",
    },
    "standard_generator": {
        "zh": "标准发电设备",
        "en": "Generator assets by class",
    },
    "standard_storage": {
        "zh": "标准储能设备",
        "en": "Storage assets by class",
    },
    "mapped_generator": {"zh": "映射后发电设备", "en": "Mapped generator"},
    "mapped_storage": {"zh": "映射后储能设备", "en": "Mapped storage"},
    "case_generator": {"zh": "算例发电设备", "en": "Case generator"},
    "case_storage": {"zh": "算例储能设备", "en": "Case storage"},
    "asset_cell_share": {
        "zh": "{title}：各空间单元类别占比（圆面积表示总容量）",
        "en": "{title}: class share by cell (circle size = total capacity)",
    },
    "standard_population": {
        "zh": "标准源网格人口",
        "en": "Population by standardized source cell",
    },
    "mapped_population": {
        "zh": "映射至标准空间单元的人口",
        "en": "Population mapped to standard spatial cells",
    },
    "population_label": {
        "zh": "log10（人口 + 1）",
        "en": "log10(population + 1)",
    },
    "load_title": {
        "zh": "{class_label}：{year}年用电量",
        "en": "{class_label}: annual electricity demand, {year}",
    },
    "load_label": {
        "zh": "{year}年用电量（TWh）",
        "en": "Annual {class_label} demand (TWh)",
    },
    "resource_title": {
        "zh": "{class_label}：{year}年平均容量因子",
        "en": "{class_label}: annual mean capacity factor, {year}",
    },
    "resource_label": {
        "zh": "{year}年平均容量因子（p.u.）",
        "en": "Annual mean {class_label} capacity factor (p.u.)",
    },
    "parameter_coverage": {"zh": "参数覆盖情况", "en": "Parameter coverage"},
    "parameter_records": {"zh": "参数记录数", "en": "Parameter records"},
    "uc_title": {"zh": "电力系统生产模拟", "en": "Power-system production simulation"},
    "time": {"zh": "时间", "en": "Time"},
    "power_mw": {"zh": "功率（MW）", "en": "Power (MW)"},
    "load": {"zh": "负荷", "en": "Load"},
    "storage_charging": {"zh": "储能充电", "en": "Storage charging"},
    "storage_discharge": {"zh": "{class_label}放电", "en": "{class_label} discharge"},
    "load_storage": {
        "zh": "负荷 + 储能充电",
        "en": "Load + storage charging",
    },
}


CLASS_LABELS = {
    "zh": {
        "bioenergy": "生物质",
        "coal": "煤电",
        "gas": "天然气",
        "geothermal": "地热",
        "nuclear": "核电",
        "hydropower": "水电",
        "solar": "光伏",
        "wind": "风电",
        "other": "其他",
        "battery": "电化学",
        "battery_storage": "电化学",
        "pumped_hydro": "抽水蓄能",
        "pumped_storage": "抽水蓄能",
        "thermal_storage": "热储能",
        "compressed_air": "压缩空气",
        "compressed_air_storage": "压缩空气",
        "capacitor_storage": "超级电容",
        "onshore": "陆上风电",
        "offshore_fixed": "海上风电",
        "offshore_floating": "海上风电",
        "offshore_unspecified": "海上风电",
        "run_of_river": "径流水电",
        "utility_scale_pv": "光伏",
        "electric_load": "电力负荷",
    },
    "en": {
        "bioenergy": "Bioenergy",
        "coal": "Coal",
        "gas": "Natural gas",
        "geothermal": "Geothermal",
        "nuclear": "Nuclear",
        "hydropower": "Hydropower",
        "solar": "Solar",
        "wind": "Wind",
        "other": "Other",
        "battery": "Battery storage",
        "battery_storage": "Battery storage",
        "pumped_hydro": "Pumped storage",
        "pumped_storage": "Pumped storage",
        "thermal_storage": "Thermal storage",
        "compressed_air": "Compressed-air storage",
        "compressed_air_storage": "Compressed-air storage",
        "capacitor_storage": "Supercapacitor storage",
        "onshore": "Onshore wind",
        "offshore_fixed": "Offshore wind",
        "offshore_floating": "Floating offshore wind",
        "offshore_unspecified": "Offshore wind",
        "run_of_river": "Run-of-river hydropower",
        "utility_scale_pv": "Utility-scale PV",
        "electric_load": "Electric load",
    },
}


def text(key: str, language: str, **values: object) -> str:
    """Return one translated display string."""

    try:
        template = TEXT[key][language]
    except KeyError as error:
        raise KeyError(f"Unknown plot text key/language: {key!r}/{language!r}") from error
    return template.format(**values)


def class_label(value: object, language: str) -> str:
    """Return a translated class label with a readable fallback."""

    key = str(value)
    fallback = key.replace("_", " ")
    if language == "en":
        fallback = fallback.title()
    return CLASS_LABELS[language].get(key, fallback)
