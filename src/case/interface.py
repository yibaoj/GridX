"""Backend-neutral regional exchange interfaces and their parameter contract."""
from __future__ import annotations

import numpy as np
import pandas as pd
import geopandas as gpd

from .model import CaseComponent

REQUIRED_COLUMNS = (
    "uid", "class", "subclass", "internal_bus_uid", "external_bus_uid",
    "rated_power_mw", "direction", "source_uid", "source_component", "geometry",
)
OPTIONAL_COLUMNS = ("voltage_kv", "current_type", "capacity_method")
PARAMETERS = {
    "power_min_pu": (0.0, "p.u."), "power_max_pu": (1.0, "p.u."),
    "energy_min_pu": (0.0, "p.u."), "energy_max_pu": (1.0, "p.u."),
    "marginal_cost_eur_per_mwh": (0.0, "EUR/MWh"),
}


def empty_interface(crs="EPSG:4326") -> CaseComponent:
    return CaseComponent(
        gpd.GeoDataFrame(columns=[*REQUIRED_COLUMNS, *OPTIONAL_COLUMNS], geometry="geometry", crs=crs),
        pd.DataFrame(columns=["uid", "name", "value", "unit", "source_id"]),
        pd.DataFrame(columns=["source_uid", "aggregate_uid", "weight"]),
    )


def build_interface(branches, internal_uids, capacities, options) -> CaseComponent:
    if options.get("direction") not in {"import", "export"}:
        raise ValueError("spatial.interface.direction must be import or export.")
    values = {name: float(options.get(name, default)) for name, (default, _) in PARAMETERS.items()}
    if not all(np.isfinite(value) for value in values.values()):
        raise ValueError("Interface parameters must be finite.")
    for prefix in ("power", "energy"):
        if not 0 <= values[f"{prefix}_min_pu"] <= values[f"{prefix}_max_pu"] <= 1:
            raise ValueError(f"Interface {prefix} bounds must satisfy 0 <= min <= max <= 1.")
    if max(values['power_min_pu'], values['energy_min_pu']) > min(values['power_max_pu'], values['energy_max_pu']):
        raise ValueError("Interface power and energy ranges do not overlap.")
    result = empty_interface(branches.crs)
    rows, parameters = [], []
    for asset, capacity in zip(branches.itertuples(index=False), capacities):
        if not np.isfinite(capacity) or capacity <= 0:
            raise ValueError(f"Interface {asset.uid} has no positive rated capacity.")
        internal = asset.from_bus_uid if asset.from_bus_uid in internal_uids else asset.to_bus_uid
        uid = f"interface:{asset.uid}"
        rows.append(dict(uid=uid, **{"class": "interface"}, subclass="boundary_exchange",
            internal_bus_uid=internal, external_bus_uid=f"external:{uid}",
            rated_power_mw=float(capacity), direction=options['direction'], source_uid=asset.uid,
            source_component="branch", geometry=asset.geometry, voltage_kv=asset.voltage_kv,
            current_type=asset.current_type, capacity_method="resolved_case_parameters"))
        parameters.extend(dict(uid=uid, name=name, value=value, unit=PARAMETERS[name][1],
                               group="economic" if name.startswith("marginal_cost") else "technical",
                               component="interface", match_result="configured",
                               source_id="case_config") for name, value in values.items())
    if not rows:
        return result
    return CaseComponent(gpd.GeoDataFrame(rows, geometry="geometry", crs=branches.crs),
        pd.DataFrame(parameters), pd.DataFrame(dict(source_uid=row['source_uid'],
        aggregate_uid=row['uid'], weight=1.0) for row in rows))


def interface_capacities(branches, parameters):
    """Compute regional interface ratings from canonical parameters, without backend defaults."""
    capacities = np.zeros(len(branches))
    for current, name, unit in (
        ("AC", "s_nom_mva_per_circuit", "MVA/circuit"),
        ("DC", "p_nom_mw_per_circuit", "MW/circuit"),
    ):
        mask = branches.current_type.str.upper().eq(current).to_numpy()
        selected = parameters.loc[parameters.name.eq(name) & parameters.unit.eq(unit)]
        if selected.uid.duplicated().any():
            raise ValueError(f"Multiple resolved {name} records for one branch.")
        values = pd.to_numeric(branches.loc[mask, "uid"].map(selected.set_index("uid").value), errors="coerce").to_numpy(float)
        if not np.isfinite(values).all() or (values <= 0).any():
            raise ValueError(f"Interface rated capacity is unresolved: supply positive {name} in {unit}.")
        capacities[mask] = values
    if len(branches) and (capacities <= 0).any():
        raise ValueError("Interface branches require AC or DC current_type.")
    # AC MVA is an explicit active-transfer proxy in the current regional model.
    return capacities * pd.to_numeric(branches.circuits, errors="raise").to_numpy(float)
