"""Historical electric-load standardization."""

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
        source_id = self.config["source_ids"][0]
        file_path = self.source(source_id)
        sheets = pd.ExcelFile(file_path, engine="openpyxl").sheet_names
        by_year = {
            int(match.group()): sheet
            for sheet in sheets
            if (match := re.search(r"20(?:1[5-9]|2[0-4])", sheet))
        }
        frames = []
        raw_names: list[str] | None = None
        for year in self.options["years"]:
            if year not in by_year:
                raise ValueError(f"Load workbook is missing year {year}.")
            frame = pd.read_excel(
                file_path,
                sheet_name=by_year[year],
                index_col=0,
                parse_dates=[0],
            )
            frame.index = pd.DatetimeIndex(pd.to_datetime(frame.index), name="time")
            if raw_names is None:
                raw_names = [
                    str(column).split(" (", 1)[0].strip() for column in frame.columns
                ]
            frame.columns = [
                self._PROVINCES[str(column).split(" (", 1)[0].strip()]
                for column in frame.columns
            ]
            frames.append(frame.apply(pd.to_numeric, errors="coerce"))
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
                "source_id": source_id,
                "crs": spatial.crs.to_string(),
            },
        )
        _write_xarray(dataset, self.output(), "demand_mw")
        return dataset
