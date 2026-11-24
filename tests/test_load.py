"""Load workbooks merge by year without silently replacing source data."""

from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

from src.standard.load import _LoadStandardizer


def _standardizer(monkeypatch, tmp_path, *, years=(2024, 2025)):
    frames = {
        "old": {"2024 load curve": pd.DataFrame(
            [[1.0, 2.0], [3.0, 4.0]],
            index=pd.date_range("2024-01-01", periods=2, freq="h"),
            columns=["Beijing (北京)", "Shandong (山东)"])},
        "new": {"2025 load curve": pd.DataFrame(
            [[20.0, 10.0], [40.0, 30.0]],
            index=pd.date_range("2025-01-01", periods=2, freq="h"),
            columns=["Shandong (山东)", "Beijing (北京)"])},
    }

    class Book:
        def __init__(self, source, **_):
            self.frames = frames[source]
            self.sheet_names = list(self.frames)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    monkeypatch.setattr(pd, "ExcelFile", Book)
    monkeypatch.setattr(pd, "read_excel", lambda book, sheet_name, **_: book.frames[sheet_name].copy())
    spatial = gpd.GeoDataFrame(
        {"name": ["北京市", "山东省"]},
        geometry=[Point(116, 40), Point(117, 36)], crs="EPSG:4326")
    manager = SimpleNamespace(
        datasets={"load": dict(source_ids=["old", "new"], output="load.nc",
                               options=dict(years=years, timezone="Asia/Shanghai", interval="1h"))},
        raw_data=SimpleNamespace(get_file=lambda source: source),
        output_root=tmp_path, load=lambda _: spatial)
    return _LoadStandardizer(manager, "load"), frames


def test_load_merges_years_aligns_provinces_and_records_source(monkeypatch, tmp_path):
    processor, _ = _standardizer(monkeypatch, tmp_path)
    data = processor.build()
    assert data.uid.values.tolist() == ["figshare:Beijing", "figshare:Shandong"]
    np.testing.assert_allclose(data.demand_mw.values[:, :, 0],
                               [[1000, 2000], [3000, 4000], [10000, 20000], [30000, 40000]])
    assert data.load_source_id.values.tolist() == ["old", "old", "new", "new"]
    assert data.attrs["source_id"] == "old;new"


def test_load_rejects_duplicate_source_year(monkeypatch, tmp_path):
    processor, frames = _standardizer(monkeypatch, tmp_path)
    frames["new"]["2024 load curve"] = frames["old"]["2024 load curve"]
    with pytest.raises(ValueError, match="Multiple load sheets provide year 2024"):
        processor.build()


def test_load_requires_all_requested_years(monkeypatch, tmp_path):
    processor, _ = _standardizer(monkeypatch, tmp_path, years=(2024, 2025, 2026))
    with pytest.raises(ValueError, match="missing years.*2026"):
        processor.build()


def test_load_historical_year_selection_ignores_future_sheets(monkeypatch, tmp_path):
    processor, _ = _standardizer(monkeypatch, tmp_path, years=(2024,))
    data = processor.build()
    assert data.sizes["time"] == 2
    assert set(data.load_source_id.values) == {"old"}
