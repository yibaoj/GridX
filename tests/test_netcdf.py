"""Regression for native crashes when a string-bearing file stays open."""

from pathlib import Path
import subprocess
import sys

import xarray as xr


def test_legacy_netcdf_strings_survive_reopening_and_gc_in_subprocess(tmp_path):
    path = tmp_path / "legacy.nc"
    xr.Dataset({"value": ("time", [1., 2.])}, coords={
        "time": [0, 1], "label": ("time", ["山东", "北京"]),
    }).to_netcdf(path, engine="netcdf4")
    script = """
import gc, sys
import xarray as xr
from src.common.io import NETCDF_ENGINE
anchor = xr.open_dataset(sys.argv[1], engine=NETCDF_ENGINE)
try:
    for i in range(200):
        with xr.open_dataset(sys.argv[1], engine=NETCDF_ENGINE) as data:
            assert data.label.values.tolist() == ['山东', '北京']
            assert data.value.values.tolist() == [1., 2.]
        if i % 10 == 0:
            gc.collect()
finally:
    anchor.close()
"""
    result = subprocess.run([sys.executable, "-X", "faulthandler", "-c", script, str(path)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stderr
