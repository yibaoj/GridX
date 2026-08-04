"""Weather-dependent resource-profile standardization."""

from __future__ import annotations

import numpy as np
import pandas as pd
import xarray as xr

from .base import _Standardizer
from .schema import _write_xarray


def _run_of_river_capacity_factor(
    runoff: xr.DataArray,
    *,
    smoothing_hours: int,
    design_quantile: float,
    environmental_flow_fraction: float,
) -> xr.DataArray:
    """Convert local runoff to run-of-river availability."""

    if smoothing_hours < 1:
        raise ValueError("run_of_river_smoothing_hours must be positive.")
    if not 0 < design_quantile < 1:
        raise ValueError("run_of_river_design_quantile must be between 0 and 1.")
    if not 0 <= environmental_flow_fraction < 1:
        raise ValueError(
            "run_of_river_environmental_flow_fraction must be in [0, 1)."
        )
    flow = runoff.clip(min=0).rolling(
        time=smoothing_hours,
        min_periods=1,
        center=True,
    ).mean()
    flow = flow.chunk({"time": -1, "y": 16, "x": 32})
    environmental_flow = flow.mean("time") * environmental_flow_fraction
    usable_flow = (flow - environmental_flow).clip(min=0)
    design_flow = usable_flow.quantile(design_quantile, dim="time")
    return (
        usable_flow / design_flow.where(design_flow > 0)
    ).clip(0, 1).fillna(0)


class _ResourceStandardizer(_Standardizer):
    def build(self) -> xr.Dataset:
        import atlite

        source_id = self.config["source_ids"][0]
        cutout = atlite.Cutout(self.source(source_id))
        variables = {
            "onshore": cutout.wind(
                turbine=self.options["onshore_turbine"],
                interpolation_method=self.options["wind_interpolation_method"],
                smooth=self.options["wind_smooth_power_curve"],
                add_cutout_windspeed=True,
                capacity_factor_timeseries=True,
            ),
            "offshore_fixed": cutout.wind(
                turbine=self.options["offshore_turbine"],
                interpolation_method=self.options["wind_interpolation_method"],
                smooth=self.options["wind_smooth_power_curve"],
                add_cutout_windspeed=True,
                capacity_factor_timeseries=True,
            ),
            "utility_scale_pv": cutout.pv(
                panel=self.options["solar_panel"],
                orientation=self.options["solar_orientation"],
                tracking=(
                    None
                    if self.options["solar_tracking"] == "none"
                    else self.options["solar_tracking"]
                ),
                clearsky_model=self.options["solar_clearsky_model"],
                capacity_factor_timeseries=True,
            ),
            "run_of_river": _run_of_river_capacity_factor(
                cutout.data["runoff"],
                smoothing_hours=self.options["run_of_river_smoothing_hours"],
                design_quantile=self.options["run_of_river_design_quantile"],
                environmental_flow_fraction=self.options[
                    "run_of_river_environmental_flow_fraction"
                ],
            ),
        }

        availability = xr.concat(
            list(variables.values()),
            dim=pd.Index(list(variables), name="class"),
        ).transpose("time", "y", "x", "class").clip(0, 1)
        y, x = np.meshgrid(
            availability["y"].values,
            availability["x"].values,
            indexing="ij",
        )
        uid = [
            f"{source_id}:{latitude:.6f}:{longitude:.6f}"
            for latitude, longitude in zip(y.ravel(), x.ravel(), strict=True)
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
                    [
                        f"{latitude:.6f},{longitude:.6f}"
                        for latitude, longitude in zip(
                            y.ravel(), x.ravel(), strict=True
                        )
                    ],
                ),
                "geometry": (
                    "uid",
                    [
                        f"POINT ({longitude:.6f} {latitude:.6f})"
                        for latitude, longitude in zip(
                            y.ravel(), x.ravel(), strict=True
                        )
                    ],
                ),
                "geometry_method": (
                    "uid", ["source_cell_centroid"] * len(uid)
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
        cutout.data.close()
        return dataset
