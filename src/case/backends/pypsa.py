"""PyPSA adapter for :class:`PowerSystemCase`."""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from ..model import CaseComponent, PowerSystemCase
from .manifest import PyPSAParameterManifest, load_pypsa_manifest


def to_pypsa(case: PowerSystemCase, *, strict: bool | None = None):
    """Return a PyPSA Network while preserving case validation metadata."""

    try:
        import pypsa
    except ImportError as error:  # pragma: no cover - optional dependency
        raise ImportError("Install pypsa to use the PyPSA case backend.") from error

    configured = bool(case.config["backend"]["pypsa"].get("strict", True))
    strict = configured if strict is None else strict
    manifest = load_pypsa_manifest(
        case.config["backend"]["pypsa"]["parameter_manifest"]
    )
    if not str(pypsa.__version__).startswith(f"{manifest.pypsa_version}."):
        raise RuntimeError(
            f"Parameter manifest targets PyPSA {manifest.pypsa_version}.x, "
            f"but {pypsa.__version__} is installed."
        )
    backend_gaps = (
        case.validation["check"].eq("backend_parameter_manifest")
        & case.validation["required"].fillna(False)
        & ~case.validation["status"].isin(["pass", "not_applicable"])
    )
    other_gaps = (
        case.validation["check"].isin(
            ["resource_profile_coverage", "load_profile_coverage", "time_alignment"]
        )
        & case.validation["status"].eq("fail")
    )
    failures = case.validation.loc[backend_gaps | other_gaps]
    if not failures.empty:
        message = (
            f"PyPSA readiness has {len(failures)} required fallback or failed checks; "
            "inspect case.validation for component-level details."
        )
        if strict:
            raise ValueError(message)
        warnings.warn(message, RuntimeWarning, stacklevel=2)

    network = pypsa.Network()
    network.set_snapshots(pd.DatetimeIndex(case.load.time.values))
    _add_carriers(network, case, manifest)
    _add_buses(network, case)
    _add_branches(network, case, manifest)
    _add_transformers(network, case, manifest)
    _add_converters(network, case, manifest)
    _add_generators(network, case, manifest)
    _add_storage(network, case, manifest)
    _add_load(network, case)
    network.meta = {
        "case_validation": case.validation.to_dict("records"),
        "parameter_complete": failures.empty,
        "case_backend": "pypsa",
        "aggregation_note": (
            "Aggregated generators are continuous dispatch assets; startup and "
            "shutdown parameters are retained in the case but not activated."
        ),
    }
    return network


def _add_carriers(
    network, case: PowerSystemCase, manifest: PyPSAParameterManifest
) -> None:
    generator = case.generator.data
    storage = case.storage.data
    names = {
        "AC", "DC", "DC branch", "ac_dc_converter",
        *(_asset_carrier(row) for _, row in generator.iterrows()),
        *(_asset_carrier(row) for _, row in storage.iterrows()),
    }
    network.add("Carrier", sorted(names))
    efficiency = _positive(
        _manifest_values(manifest, case.generator, "generator_efficiency"),
        manifest.spec("generator_efficiency").fallback,
    )
    emissions = _manifest_values(
        manifest, case.generator, "generator_co2_intensity"
    )
    emissions = np.divide(emissions, efficiency, out=np.zeros_like(emissions), where=efficiency > 0)
    weights = pd.to_numeric(generator.capacity_mw, errors="coerce").fillna(0)
    table = pd.DataFrame({
        "carrier": [_asset_carrier(row) for _, row in generator.iterrows()],
        "emissions": emissions,
        "weight": weights,
    })
    table["weighted"] = table["emissions"] * table["weight"]
    grouped = table.groupby("carrier").agg(weighted=("weighted", "sum"), weight=("weight", "sum"))
    network.carriers.loc[grouped.index, "co2_emissions"] = (
        grouped["weighted"] / grouped["weight"].replace(0, np.nan)
    ).fillna(0.0)


def _add_buses(network, case: PowerSystemCase) -> None:
    buses = case.network.bus.data.to_crs("EPSG:4326")
    points = buses.geometry.map(
        lambda geometry: geometry
        if geometry.geom_type == "Point" else geometry.representative_point()
    )
    network.add(
        "Bus", buses["uid"].astype(str),
        v_nom=pd.to_numeric(buses["voltage_kv"], errors="coerce").fillna(1.0).to_numpy(),
        x=points.x.to_numpy(), y=points.y.to_numpy(),
        carrier=buses.get("current_type", "AC").astype("string").fillna("AC").to_numpy(),
    )


def _add_branches(
    network, case: PowerSystemCase, manifest: PyPSAParameterManifest
) -> None:
    data = case.network.branch.data
    length = pd.to_numeric(data["length_km"], errors="coerce").fillna(0.0)
    effective_length = length.clip(lower=0.001).to_numpy()
    r = _positive(
        _manifest_values(manifest, case.network.branch, "branch_ac_r"),
        manifest.spec("branch_ac_r").fallback,
    ) * effective_length
    x = _positive(
        _manifest_values(manifest, case.network.branch, "branch_ac_x"),
        manifest.spec("branch_ac_x").fallback,
    ) * effective_length
    capacitance = _manifest_values(
        manifest, case.network.branch, "branch_ac_b"
    )
    b = 2 * np.pi * 50 * capacitance * 1e-9 * length.to_numpy()
    circuits = pd.to_numeric(data.get("circuits", 1.0), errors="coerce").fillna(1.0)
    s_nom = _positive(
        _manifest_values(manifest, case.network.branch, "branch_ac_s_nom"),
        manifest.spec("branch_ac_s_nom").fallback,
    ) * circuits.to_numpy()
    dc_p_nom = _positive(
        _manifest_values(manifest, case.network.branch, "branch_dc_p_nom"),
        manifest.spec("branch_dc_p_nom").fallback,
    ) * circuits.to_numpy()
    dc_efficiency = _positive(
        _manifest_values(
            manifest, case.network.branch, "branch_dc_efficiency"
        ),
        manifest.spec("branch_dc_efficiency").fallback,
    )
    extendable = bool(case.config["backend"]["pypsa"]["branch_extendable"])
    current = data.get("current_type", pd.Series("AC", index=data.index)).astype(
        "string"
    ).fillna("AC")
    ac = current.str.upper().eq("AC").to_numpy()
    if ac.any():
        network.add(
            "Line", data.loc[ac, "uid"].astype(str),
            bus0=data.loc[ac, "from_bus_uid"].astype(str).to_numpy(),
            bus1=data.loc[ac, "to_bus_uid"].astype(str).to_numpy(),
            r=np.nan_to_num(r[ac]), x=np.nan_to_num(x[ac]),
            b=np.nan_to_num(b[ac]), s_nom=np.nan_to_num(s_nom[ac]),
            length=length.loc[ac].to_numpy(), carrier="AC",
            s_nom_extendable=extendable,
        )
    if (~ac).any():
        network.add(
            "Link", data.loc[~ac, "uid"].astype(str),
            bus0=data.loc[~ac, "from_bus_uid"].astype(str).to_numpy(),
            bus1=data.loc[~ac, "to_bus_uid"].astype(str).to_numpy(),
            p_nom=np.nan_to_num(dc_p_nom[~ac]), p_min_pu=-1.0,
            efficiency=dc_efficiency[~ac], carrier="DC branch",
            p_nom_extendable=extendable,
        )


def _add_transformers(
    network, case: PowerSystemCase, manifest: PyPSAParameterManifest
) -> None:
    data = case.network.transformer.data
    sn = _positive(
        _manifest_values(manifest, case.network.transformer, "transformer_s_nom"),
        manifest.spec("transformer_s_nom").fallback,
    )
    vk = _positive(
        _manifest_values(manifest, case.network.transformer, "transformer_vk"),
        manifest.spec("transformer_vk").fallback,
    ) / 100
    r = _positive(
        _manifest_values(manifest, case.network.transformer, "transformer_vkr"),
        manifest.spec("transformer_vkr").fallback,
    ) / 100
    x = np.sqrt(np.maximum(vk ** 2 - r ** 2, 0))
    network.add(
        "Transformer", data.uid.astype(str),
        bus0=data.from_bus_uid.astype(str).to_numpy(),
        bus1=data.to_bus_uid.astype(str).to_numpy(),
        s_nom=np.nan_to_num(sn), r=np.nan_to_num(r), x=np.nan_to_num(x),
        s_nom_extendable=bool(
            case.config["backend"]["pypsa"]["transformer_extendable"]
        ),
    )


def _add_converters(
    network, case: PowerSystemCase, manifest: PyPSAParameterManifest
) -> None:
    data = case.network.converter.data
    network.add(
        "Link", data.uid.astype(str),
        bus0=data.from_bus_uid.astype(str).to_numpy(),
        bus1=data.to_bus_uid.astype(str).to_numpy(),
        p_nom=_positive(
            _manifest_values(manifest, case.network.converter, "converter_p_nom"),
            manifest.spec("converter_p_nom").fallback,
        ),
        efficiency=_positive(
            _manifest_values(
                manifest, case.network.converter, "converter_efficiency"
            ),
            manifest.spec("converter_efficiency").fallback,
        ),
        carrier="ac_dc_converter",
        p_nom_extendable=bool(
            case.config["backend"]["pypsa"]["converter_extendable"]
        ),
    )


def _add_generators(
    network, case: PowerSystemCase, manifest: PyPSAParameterManifest
) -> None:
    data = case.generator.data
    efficiency = _positive(
        _manifest_values(manifest, case.generator, "generator_efficiency"),
        manifest.spec("generator_efficiency").fallback,
    )
    vom = _manifest_values(manifest, case.generator, "generator_vom")
    fuel = _manifest_values(manifest, case.generator, "generator_fuel")
    emissions = _manifest_values(
        manifest, case.generator, "generator_co2_intensity"
    )
    carbon_price = float(
        case.config["backend"]["pypsa"].get("carbon_price_eur_per_t_co2", 0.0)
    )
    thermal_cost = np.divide(
        fuel + carbon_price * emissions,
        efficiency,
        out=np.zeros_like(fuel),
        where=efficiency > 0,
    )
    variable_mask = data["class"].isin(["wind", "solar", "hydropower"])
    minimum_output = _manifest_values(
        manifest, case.generator, "generator_minimum_output"
    )
    minimum_output[variable_mask.to_numpy()] = 0.0
    kwargs = {
        "bus": data.bus_uid.astype(str).to_numpy(),
        "p_nom": pd.to_numeric(data.capacity_mw, errors="coerce").fillna(0).to_numpy(),
        "carrier": [_asset_carrier(row) for _, row in data.iterrows()],
        "efficiency": efficiency,
        "marginal_cost": vom + thermal_cost,
        "p_min_pu": minimum_output,
        "ramp_limit_up": _manifest_values(
            manifest, case.generator, "generator_ramp_up"
        ) * _time_step_hours(case),
        "ramp_limit_down": _manifest_values(
            manifest, case.generator, "generator_ramp_down"
        ) * _time_step_hours(case),
        "committable": False,
        "p_nom_extendable": bool(
            case.config["backend"]["pypsa"]["generator_extendable"]
        ),
        "capital_cost": _manifest_values(
            manifest, case.generator, "generator_capital_cost"
        ),
    }
    network.add("Generator", data.uid.astype(str), **kwargs)
    profiles = _generator_profiles(case)
    availability = pd.DataFrame(
        1.0, index=network.snapshots, columns=network.generators.index
    )
    variable = data.loc[variable_mask, "uid"].astype(str)
    availability.loc[:, availability.columns.isin(variable)] = 0.0
    if not profiles.empty:
        availability.loc[:, profiles.columns] = profiles.reindex(
            network.snapshots
        ).to_numpy()
    network.generators_t.p_max_pu = availability


def _add_storage(
    network, case: PowerSystemCase, manifest: PyPSAParameterManifest
) -> None:
    data = case.storage.data
    efficiency = _positive(
        _manifest_values(manifest, case.storage, "storage_efficiency"),
        manifest.spec("storage_efficiency").fallback,
    )
    network.add(
        "StorageUnit", data.uid.astype(str),
        bus=data.bus_uid.astype(str).to_numpy(),
        p_nom=pd.to_numeric(data.power_capacity_mw, errors="coerce").fillna(0).to_numpy(),
        max_hours=_positive(
            _manifest_values(manifest, case.storage, "storage_max_hours"),
            manifest.spec("storage_max_hours").fallback,
        ),
        carrier=[_asset_carrier(row) for _, row in data.iterrows()],
        efficiency_store=np.sqrt(efficiency),
        efficiency_dispatch=np.sqrt(efficiency),
        standing_loss=_manifest_values(
            manifest, case.storage, "storage_standing_loss"
        ),
        cyclic_state_of_charge=_manifest_values(
            manifest, case.storage, "storage_cyclic_soc"
        ).astype(bool),
        marginal_cost=_manifest_values(
            manifest, case.storage, "storage_marginal_cost"
        ),
        p_nom_extendable=bool(
            case.config["backend"]["pypsa"]["storage_extendable"]
        ),
        capital_cost=_manifest_values(
            manifest, case.storage, "storage_capital_cost"
        ),
    )


def _add_load(network, case: PowerSystemCase) -> None:
    data = case.load["demand_mw"].sum("class")
    names = [f"load:{uid}" for uid in data.uid.values.astype(str)]
    network.add(
        "Load", names,
        bus=data.uid.values.astype(str),
        p_set=pd.DataFrame(data.values, index=network.snapshots, columns=names),
    )


def _generator_profiles(case: PowerSystemCase) -> pd.DataFrame:
    resource = case.resource["availability_pu"]
    classes = set(resource["class"].values.astype(str))
    resource_uids = set(resource.uid.values.astype(str))
    mapping = case.config["backend"]["pypsa"].get("resource_class_mapping", {})
    profiles = {}
    for asset in case.generator.data.itertuples(index=False):
        subclass = str(asset.subclass)
        resource_class = mapping.get(subclass)
        if resource_class not in classes:
            continue
        members = case.generator.membership.loc[
            case.generator.membership["aggregate_uid"].eq(asset.uid)
        ]
        values = []
        weights = []
        for member in members.itertuples(index=False):
            if str(member.source_spatial_uid) not in resource_uids:
                continue
            values.append(resource.sel(
                uid=member.source_spatial_uid, **{"class": resource_class}
            ).values)
            weights.append(member.source_capacity_mw)
        if values:
            profiles[asset.uid] = np.average(values, axis=0, weights=weights)
    return pd.DataFrame(profiles, index=pd.DatetimeIndex(resource.time.values))


def _manifest_values(
    manifest: PyPSAParameterManifest,
    component: CaseComponent,
    key: str,
) -> np.ndarray:
    return manifest.values(key, component.data, component.parameter)


def _positive(values: np.ndarray, fallback: object) -> np.ndarray:
    """Replace unavailable electrical values with an explicit positive fallback."""

    result = np.asarray(values, dtype=float).copy()
    result[~np.isfinite(result) | (result <= 0)] = float(fallback)
    return result


def _time_step_hours(case: PowerSystemCase) -> float:
    return pd.Timedelta(str(case.load.attrs["time_step"])) / pd.Timedelta("1h")


def _asset_carrier(asset: pd.Series) -> str:
    subclass = asset.get("subclass")
    return (
        str(asset["class"])
        if pd.isna(subclass) else f"{asset['class']}:{subclass}"
    )
