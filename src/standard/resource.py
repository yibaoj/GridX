"""Weather-dependent resource-profile standardization."""

from __future__ import annotations

import numpy as np
import pandas as pd
import xarray as xr

from .base import _Standardizer
from .schema import _write_xarray


class _ResourceStandardizer(_Standardizer):
    def build(self) -> xr.Dataset:
        import atlite

        source_id = self.config["source_ids"][0]
        cutout = atlite.Cutout(self.source(source_id))
        variables = {
            "onshore": cutout.wind(
                turbine=self.options["onshore_turbine"],
                capacity_factor_timeseries=True,
            ),
            "offshore_fixed": cutout.wind(
                turbine=self.options["offshore_turbine"],
                capacity_factor_timeseries=True,
            ),
            "utility_scale_pv": cutout.pv(
                panel=self.options["solar_panel"],
                orientation=self.options["solar_orientation"],
                capacity_factor_timeseries=True,
            ),
        }
        runoff = cutout.data["runoff"].clip(min=0)
        for subclass, window, mean in (
            ("run_of_river", 24, self.options["run_of_river_mean_pu"]),
            ("reservoir", 168, self.options["reservoir_mean_pu"]),
        ):
            profile = runoff.rolling(time=window, min_periods=1, center=True).mean()
            variables[subclass] = (
                profile / profile.mean("time").where(profile.mean("time") > 0) * mean
            ).clip(0, 1).fillna(0)

        availability = xr.concat(
            list(variables.values()),
            dim=pd.Index(list(variables), name="class"),
        ).transpose("time", "y", "x", "class")
        y, x = np.meshgrid(availability["y"].values, availability["x"].values, indexing="ij")
        uid = [
            f"{source_id}:{row}:{column}"
            for row in range(availability.sizes["y"])
            for column in range(availability.sizes["x"])
        ]
        dataset = xr.Dataset(
            {
                "availability_pu": (
                    ("time", "uid", "class"),
                    availability.data.reshape(
                        availability.sizes["time"], len(uid), len(variables)
                    ).astype("float32"),
                )
            },
            coords={
                "time": availability["time"].values,
                "uid": uid,
                "class": list(variables),
                "location": (
                    "uid",
                    [f"{latitude:.6f},{longitude:.6f}" for latitude, longitude in zip(y.ravel(), x.ravel(), strict=True)],
                ),
                "geometry": (
                    "uid",
                    [f"POINT ({longitude:.6f} {latitude:.6f})" for latitude, longitude in zip(y.ravel(), x.ravel(), strict=True)],
                ),
                "geometry_method": (
                    "uid", ["source_grid_centroid"] * len(uid)
                ),
            },
            attrs={
                "standard_dataset_id": self.dataset_id,
                "timezone": "UTC",
                "time_step": self.options.get("time_step", "1h"),
                "source_unit": "ERA5 meteorological fields",
                "unit": "p.u.",
                "source_id": source_id,
                "crs": "EPSG:4326",
            },
        )
        _write_xarray(dataset, self.output(), "availability_pu")
        return dataset
