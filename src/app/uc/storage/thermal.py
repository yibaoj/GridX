"""Fixed-efficiency solar thermal storage for UC dispatch."""

import math

import pandas as pd


def add_sensible_heat(network, case, snapshots, assets, storage_options):
    """Replace selected sensible-heat units with a heat store and power block."""
    if not assets["subclass"].eq("sensible_heat").all():
        raise ValueError("sensible_heat requires sensible-heat assets.")
    options = storage_options["thermal"]
    legacy_options = options if "models" not in storage_options else {}
    initial = float(storage_options.get("initial_soc_pu", 0.5))
    final = float(storage_options.get("final_soc_pu", 0.5))
    if options["solar_profile"] != "pv_proxy":
        raise ValueError("The sensible-heat model currently supports the explicit pv_proxy profile.")

    if case.config["backend"]["pypsa"]["storage_extendable"]:
        raise ValueError("The sensible-heat model requires fixed storage capacity.")

    # The available PV curve is a demonstration proxy, not a DNI collector model.
    resource = case.resource["availability_pu"]
    for uid, asset in assets.set_index("uid").iterrows():
        efficiency = _thermal_parameter(case, uid, legacy_options, "power_block_efficiency", "p.u.")
        loss = _thermal_parameter(case, uid, legacy_options, "standing_loss_per_hour", "p.u./h")
        solar_multiple = _thermal_parameter(case, uid, legacy_options, "solar_multiple", "p.u.")
        if not (0 < efficiency <= 1 and 0 <= loss < 1 and
                solar_multiple > 0 and 0 <= initial <= 1 and 0 <= final <= 1):
            raise ValueError(f"Invalid sensible-heat efficiency, loss, solar multiple or SOC for {uid}.")
        unit = network.storage_units.loc[uid]
        cell = str(asset["spatial_uid"])
        profile = resource.sel(uid=cell, **{"class": "utility_scale_pv"}).to_series().reindex(snapshots)
        if profile.isna().any() or not profile.between(0, 1).all():
            raise ValueError(f"Missing or invalid solar profile for {uid} ({cell}).")
        heat_power = float(unit.p_nom) / efficiency
        # The standard duration is electric-equivalent hours at rated output.
        energy = heat_power * float(unit.max_hours)
        if not math.isfinite(energy) or energy <= 0:
            raise ValueError(f"Invalid sensible-heat capacity for {uid}.")
        if "thermal_storage" not in network.carriers.index:
            network.add("Carrier", "thermal_storage")
        if "solar_heat" not in network.carriers.index:
            network.add("Carrier", "solar_heat")
        heat_bus = f"heat:{uid}"
        network.add("Bus", heat_bus, carrier="solar_heat")
        network.add("Generator", f"collector:{uid}", bus=heat_bus,
                    carrier="solar_heat", p_nom=solar_multiple * heat_power,
                    p_max_pu=profile.reindex(network.snapshots, fill_value=0))
        bound = pd.Series(0.0, index=network.snapshots)
        bound.loc[snapshots[-1]] = final
        upper = pd.Series(1.0, index=network.snapshots)
        upper.loc[snapshots[-1]] = final
        # PyPSA applies losses to preceding variable states. Apply the first
        # interval's loss here because its preceding state is a fixed input.
        first_hours = float(network.snapshot_weightings.at[snapshots[0], "stores"])
        network.add("Store", uid, bus=heat_bus, carrier="thermal_storage",
                    e_nom=energy, e_initial=initial * energy * (1 - loss) ** first_hours, e_min_pu=bound,
                    e_max_pu=upper, standing_loss=loss)
        network.add("Link", f"power_block:{uid}", bus0=heat_bus, bus1=unit.bus,
                    carrier="thermal_storage", p_nom=heat_power, efficiency=efficiency,
                    marginal_cost=float(unit.marginal_cost) * efficiency)
        network.remove("StorageUnit", uid)


def _thermal_parameter(case, uid, legacy_options, name, unit):
    parameter_name = "thermal_" + name
    # Old, saved demo configurations can still supply their original assumptions.
    if name in legacy_options:
        return float(legacy_options[name])
    parameters = case.storage.parameter
    selected = parameters.loc[
        parameters["uid"].eq(uid) & parameters["name"].eq(parameter_name), ["value", "unit"]
    ].dropna()
    if len(selected) != 1 or selected.iloc[0]["unit"] != unit:
        raise ValueError(f"{uid} requires one resolved {parameter_name} in {unit}; rebuild Case parameters.")
    value = float(selected.iloc[0]["value"])
    if not math.isfinite(value):
        raise ValueError(f"{uid} has nonfinite {parameter_name}.")
    return value
