"""Weather-dependent resource-profile standardization."""

from __future__ import annotations

import pandas as pd
import xarray as xr

from .base import _Standardizer


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
        for technology, window, mean in (
            ("run_of_river", 24, self.options["run_of_river_mean_pu"]),
            ("reservoir", 168, self.options["reservoir_mean_pu"]),
        ):
            profile = runoff.rolling(
                time=window, min_periods=1, center=True
            ).mean()
            variables[technology] = (
                profile / profile.mean("time").where(profile.mean("time") > 0) * mean
            ).clip(0, 1).fillna(0)
        availability = xr.concat(
            list(variables.values()),
            dim=pd.Index(list(variables), name="technology"),
        ).astype("float32")
        dataset = xr.Dataset(
            {"availability_pu": availability},
            coords={
                "type": (
                    "technology",
                    ["wind", "wind", "solar", "hydropower", "hydropower"],
                )
            },
            attrs={
                "source_id": source_id,
                "unit": "p.u.",
                "timezone": "UTC",
                "time_reference": "interval_start",
            },
        )
        path = self.output()
        path.parent.mkdir(parents=True, exist_ok=True)
        dataset.to_netcdf(
            path,
            encoding={"availability_pu": {"zlib": True, "complevel": 4}},
        )
        return dataset
