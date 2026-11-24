"""Merge historical and scenario electric-load workbooks."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
import xarray as xr

from .base import _Standardizer
from .schema import _write_xarray


class _LoadStandardizer(_Standardizer):
    _PROVINCES = {
        "Anhui": "安徽省",
        "Beijing": "北京市",
        "Chongqing": "重庆市",
        "Fujian": "福建省",
        "Gansu": "甘肃省",
        "Guangdong": "广东省",
        "Guangxi": "广西壮族自治区",
        "Guizhou": "贵州省",
        "Hainan": "海南省",
        "Hebei": "河北省",
        "Heilongjiang": "黑龙江省",
        "Henan": "河南省",
        "Hubei": "湖北省",
        "Hunan": "湖南省",
        "Inner Mongolia": "内蒙古自治区",
        "Jiangsu": "江苏省",
        "Jiangxi": "江西省",
        "Jilin": "吉林省",
        "Liaoning": "辽宁省",
        "Ningxia": "宁夏回族自治区",
        "Qinghai": "青海省",
        "Shaanxi": "陕西省",
        "Shandong": "山东省",
        "Shanghai": "上海市",
        "Shanxi": "山西省",
        "Sichuan": "四川省",
        "Tianjin": "天津市",
        "Tibet": "西藏自治区",
        "Xinjiang": "新疆维吾尔自治区",
        "Yunnan": "云南省",
        "Zhejiang": "浙江省",
    }

    def build(self) -> xr.Dataset:
        source_ids = self.config["source_ids"]
        years = set(self.options["years"])
        found_years: set[int] = set()
        frames = []
        provenance = []
        raw_names: list[str] | None = None
        for source_id in source_ids:
            with pd.ExcelFile(self.source(source_id), engine="openpyxl") as book:
                for sheet in book.sheet_names:
                    match = re.search(r"(?<!\d)(20\d{2})(?!\d)", sheet)
                    if not match or (year := int(match.group())) not in years:
                        continue
                    if year in found_years:
                        raise ValueError(f"Multiple load sheets provide year {year}.")
                    frame = pd.read_excel(
                        book, sheet_name=sheet, index_col=0, parse_dates=[0]
                    )
                    frame.index = pd.DatetimeIndex(
                        pd.to_datetime(frame.index), name="time"
                    )
                    if not (frame.index.year == year).all():
                        raise ValueError(
                            f"Load sheet {sheet!r} contains timestamps outside {year}."
                        )
                    names = [
                        str(column).split(" (", 1)[0].strip()
                        for column in frame.columns
                    ]
                    if raw_names is None:
                        raw_names = names
                    if len(set(names)) != len(names) or set(names) != set(raw_names):
                        raise ValueError("Load sources have inconsistent province columns.")
                    frame.columns = names
                    frame = frame.reindex(columns=raw_names)
                    frame.columns = [self._PROVINCES[name] for name in raw_names]
                    frames.append(frame.apply(pd.to_numeric, errors="coerce"))
                    provenance.append(pd.Series(source_id, index=frame.index))
                    found_years.add(year)
        if missing := years.difference(found_years):
            raise ValueError(f"Load workbooks are missing years {sorted(missing)}.")
        load = pd.concat(frames).sort_index()
        if load.index.has_duplicates or load.isna().any().any():
            raise ValueError("Load timestamps or values are incomplete.")
        region_names = list(load.columns)
        source_names = raw_names or region_names
        uid = [f"figshare:{name}" for name in source_names]
        spatial = self.manager.load("spatial").set_index("name")
        missing_locations = set(region_names).difference(spatial.index)
        if missing_locations:
            raise ValueError(
                "Load locations are missing from spatial data: "
                f"{sorted(missing_locations)}"
            )
        geometry = spatial.loc[region_names].geometry.to_wkt().to_numpy()
        dataset = xr.Dataset(
            {
                "demand_mw": (
                    ("time", "uid", "class"),
                    load.to_numpy(dtype="float32")[:, :, None] * 1000,
                )
            },
            coords={
                "time": load.index.to_numpy(),
                "uid": uid,
                "class": ["electric_load"],
                "load_source_id": (
                    "time", pd.concat(provenance).reindex(load.index).to_numpy()
                ),
                "location": ("uid", region_names),
                "geometry": ("uid", geometry),
                "geometry_method": (
                    "uid", ["inferred_from_spatial_unit"] * len(uid)
                ),
            },
            attrs={
                "standard_dataset_id": self.dataset_id,
                "timezone": self.options["timezone"],
                "time_step": self.options["interval"],
                "source_unit": "GWh per hourly interval",
                "unit": "MW",
                "source_id": ";".join(source_ids),
                "crs": spatial.crs.to_string(),
            },
        )
        _write_xarray(dataset, self.output(), "demand_mw")
        return dataset
