"""Weather-dependent resource-profile standardization."""

from __future__ import annotations

from contextlib import ExitStack
import numpy as np
import pandas as pd
import xarray as xr

from .base import _Standardizer
from .schema import _write_xarray


def _merge_era5_sources(datasets, source_ids):
    """Concatenate disjoint periods on one identical grid, retaining provenance."""

    parts = []
    reference = datasets[0]
    for data, source_id in zip(datasets, source_ids, strict=True):
        for name in ("x", "y"):
            if not data[name].equals(reference[name]):
                raise ValueError(f"{source_id} uses a different ERA5 grid; map sources separately.")
        if set(data.data_vars) != set(reference.data_vars):
            raise ValueError(f"{source_id} has different prepared ERA5 variables.")
        parts.append(data.assign_coords(weather_source_id=("time", [source_id] * data.sizes["time"])))
    result = xr.concat(parts, dim="time", data_vars="minimal", coords="minimal",
                       compat="equals", join="exact").sortby("time")
    if pd.DatetimeIndex(result.time.values).has_duplicates:
        raise ValueError("ERA5 source timestamps overlap; select non-overlapping sources.")
    return result


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

        source_ids = self.config["source_ids"]
        with ExitStack() as stack:
            cutouts = []
            for source_id in source_ids:
                cutout = atlite.Cutout(self.source(source_id))
                stack.callback(cutout.data.close)
                cutouts.append(cutout)
            cutout = cutouts[0]
            cutout.data = _merge_era5_sources([item.data for item in cutouts], source_ids)
            return self._build_profiles(cutout, source_ids)

    def _build_profiles(self, cutout, source_ids):
        from atlite.convert import convert_pv, convert_wind
        from atlite.pv.orientation import get_orientation
        from atlite.resource import get_solarpanelconfig, get_windturbineconfig, windturbine_smooth

        # Cutout.wind/pv eagerly load their result; use the same lazy converters.
        def wind(name):
            turbine = get_windturbineconfig(name, add_cutout_windspeed=True)
            if smooth := self.options["wind_smooth_power_curve"]:
                turbine = windturbine_smooth(turbine, params=smooth)
            return convert_wind(cutout.data, turbine, self.options["wind_interpolation_method"])

        panel = self.options["solar_panel"]
        if isinstance(panel, str):
            panel = get_solarpanelconfig(panel)
        variables = {
            "onshore": wind(self.options["onshore_turbine"]),
            "offshore_fixed": wind(self.options["offshore_turbine"]),
            "utility_scale_pv": convert_pv(
                cutout.data,
                panel=panel,
                orientation=get_orientation(self.options["solar_orientation"]),
                tracking=(
                    None
                    if self.options["solar_tracking"] == "none"
                    else self.options["solar_tracking"]
                ),
                clearsky_model=self.options["solar_clearsky_model"],
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
            f"era5:{latitude:.6f}:{longitude:.6f}"
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
                "weather_source_id": ("time", cutout.data.weather_source_id.values),
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
                "source_id": ";".join(source_ids),
                "crs": "EPSG:4326",
            },
        )
        import dask

        with dask.config.set(scheduler="synchronous"):
            _write_xarray(dataset, self.output(), "availability_pu")
        return dataset
