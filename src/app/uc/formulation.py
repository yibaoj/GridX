"""Readable mathematical inventory of the configured PyPSA UC/ED model."""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from ...case import PowerSystemCase


def formulation_table(
    case: PowerSystemCase,
    config: dict,
    *,
    model: object | None = None,
    sections: str | Iterable[str] | None = None,
    active_only: bool = False,
) -> pd.DataFrame:
    """List sets, parameters, variables, objective, and constraints in order."""

    continuous = str(config["general"]["commitment_mode"]) == "continuous"
    branches = case.network.branch.data
    branch_current = branches.get(
        "current_type", pd.Series("AC", index=branches.index)
    ).astype("string").fillna("AC").str.upper()
    has = {
        "branch": branch_current.eq("AC").any(),
        "transformer": not case.network.transformer.data.empty,
        "converter": not case.network.converter.data.empty,
        "link": branch_current.eq("DC").any()
        or not case.network.converter.data.empty,
        "storage": not case.storage.data.empty,
        "shedding": bool(config["reliability"]["enabled"]),
    }
    extendable = any(
        bool(case.config["backend"]["pypsa"][name])
        for name in (
            "branch_extendable", "transformer_extendable",
            "converter_extendable", "generator_extendable",
            "storage_extendable",
        )
    )
    rows = _rows(continuous=continuous, has=has, extendable=extendable)
    table = pd.DataFrame(rows)
    table["realized"] = _realized(table, model)
    if sections is not None:
        selected = [sections] if isinstance(sections, str) else list(sections)
        table = table.loc[table["section"].isin(selected)]
    if active_only:
        table = table.loc[table["active"]]
    return table.reset_index(drop=True)


def _rows(*, continuous: bool, has: dict[str, bool], extendable: bool) -> list[dict]:
    rows = []

    def add(
        section: str,
        item: str,
        symbol: str,
        indices: str,
        latex: str,
        description: str,
        active: bool = True,
        pypsa_names: str = "",
    ) -> None:
        rows.append({
            "section": section,
            "item": item,
            "symbol": symbol,
            "indices": indices,
            "latex": latex,
            "description": description,
            "active": bool(active),
            "pypsa_names": pypsa_names,
        })

    # Sets and indices.
    for item, symbol, description in (
        ("snapshots", r"\mathcal{T}", "Optimization snapshots"),
        ("buses", r"\mathcal{N}", "Electrical buses"),
        ("generators", r"\mathcal{G}", "Aggregated generators"),
        ("ac branches", r"\mathcal{L}", "Passive AC lines"),
        ("transformers", r"\mathcal{H}", "AC transformers"),
        ("links", r"\mathcal{K}", "DC branches and AC/DC converters"),
        ("storage", r"\mathcal{S}", "Storage units"),
    ):
        add("sets", item, symbol, "-", symbol, description)

    # Parameters used by the active and standard UC formulations.
    parameters = (
        ("load", r"D_{n,t}", "n,t", "Bus demand (MW)"),
        ("snapshot weight", r"w_t", "t", "Snapshot duration/objective weight"),
        ("generator capacity", r"\bar P_g", "g", "Installed generator capacity (MW)"),
        ("availability", r"a_{g,t}", "g,t", "Available capacity fraction"),
        ("minimum output", r"\underline p_g", "g", "Minimum output fraction"),
        ("marginal cost", r"c_g", "g", "Fuel, variable O&M, and carbon cost (EUR/MWh)"),
        ("ramp up", r"R_g^{\uparrow}", "g", "Ramp-up fraction per snapshot"),
        ("ramp down", r"R_g^{\downarrow}", "g", "Ramp-down fraction per snapshot"),
        ("branch capacity", r"\bar F_l", "l", "Branch thermal capacity (MVA/MW)"),
        ("branch reactance", r"x_l", "l", "Series reactance used by passive AC flow"),
        ("link efficiency", r"\eta_k", "k", "DC/converter transfer efficiency"),
        ("storage power", r"\bar P_s", "s", "Storage charge/discharge power (MW)"),
        ("storage duration", r"h_s", "s", "Energy-to-power duration (h)"),
        ("storage efficiencies", r"\eta_s^{ch},\eta_s^{dis}", "s", "Charge and discharge efficiencies"),
        ("standing loss", r"\lambda_s", "s", "Hourly standing-loss fraction"),
        ("VOLL", r"c^{shed}", "n", "Load-shedding marginal cost (EUR/MWh)"),
        ("minimum up time", r"T_g^{up}", "g", "Minimum online duration"),
        ("minimum down time", r"T_g^{down}", "g", "Minimum offline duration"),
        ("startup cost", r"C_g^{su}", "g", "Generator startup cost"),
        ("shutdown cost", r"C_g^{sd}", "g", "Generator shutdown cost"),
    )
    for item, symbol, indices, description in parameters:
        commitment_parameter = item in {
            "minimum up time", "minimum down time", "startup cost", "shutdown cost"
        }
        add(
            "parameters", item, symbol, indices, symbol, description,
            active=not continuous or not commitment_parameter,
        )

    # Decision variables.
    add("variables", "generator dispatch", r"p_{g,t}", "g,t", r"p_{g,t}\in\mathbb{R}", "Generator dispatch", pypsa_names="Generator-p")
    add("variables", "passive branch flow", r"f_{l,t}", "l,t", r"f_{l,t}\in\mathbb{R}", "AC line/transformer flow", has["branch"] or has["transformer"], "Line-s;Transformer-s")
    add("variables", "link flow", r"f_{k,t}", "k,t", r"f_{k,t}\in\mathbb{R}", "DC/converter transfer", has["link"], "Link-p")
    add("variables", "storage discharge", r"p^{dis}_{s,t}", "s,t", r"p^{dis}_{s,t}\ge0", "Storage discharge", has["storage"], "StorageUnit-p_dispatch")
    add("variables", "storage charge", r"p^{ch}_{s,t}", "s,t", r"p^{ch}_{s,t}\ge0", "Storage charging", has["storage"], "StorageUnit-p_store")
    add("variables", "state of charge", r"e_{s,t}", "s,t", r"e_{s,t}\ge0", "Stored energy", has["storage"], "StorageUnit-state_of_charge")
    add("variables", "load shedding", r"p^{shed}_{n,t}", "n,t", r"p^{shed}_{n,t}\ge0", "Reliability slack represented as a generator", has["shedding"], "Generator-p")
    add("variables", "commitment status", r"u_{g,t}", "g,t", r"u_{g,t}\in\{0,1\}", "Binary online status", not continuous, "Generator-status")
    add("variables", "startup", r"v_{g,t}", "g,t", r"v_{g,t}\in\{0,1\}", "Binary startup indicator", not continuous, "Generator-start_up")
    add("variables", "shutdown", r"z_{g,t}", "g,t", r"z_{g,t}\in\{0,1\}", "Binary shutdown indicator", not continuous, "Generator-shut_down")
    add("variables", "new capacity", r"P_i^{new}", "i", r"P_i^{new}\ge0", "Extendable nominal capacity", extendable, "Generator-p_nom;Line-s_nom;Link-p_nom;StorageUnit-p_nom")

    # Objective.
    add(
        "objective", "total system cost", r"\min C", "-",
        r"\min\sum_t w_t[\sum_g c_g p_{g,t}+\sum_n c^{shed}p^{shed}_{n,t}]",
        "Current continuous objective; storage marginal costs are included when nonzero.",
    )
    add(
        "objective", "commitment costs", r"C^{UC}", "g,t",
        r"\sum_{g,t}(C_g^{su}v_{g,t}+C_g^{sd}z_{g,t})",
        "PyPSA UC startup and shutdown terms", not continuous,
    )
    add(
        "objective", "investment costs", r"C^{inv}", "i",
        r"\sum_i c_i^{cap}P_i^{new}",
        "Capacity-expansion term", extendable,
    )

    # System-level constraints first.
    add(
        "system constraints", "nodal power balance", r"\lambda_{n,t}", "n,t",
        r"\sum_{g\in n}p_{g,t}+p^{dis}_{n,t}+p^{shed}_{n,t}-p^{ch}_{n,t}-\sum_l K_{n,l}f_{l,t}=D_{n,t}",
        "Kirchhoff current balance at every bus and snapshot", True,
        "Bus-nodal_balance",
    )
    add(
        "system constraints", "Kirchhoff voltage law", r"\mu_{c,t}", "cycle c,t",
        r"\sum_{l\in\mathcal{L}}C_{l,c}x_l f_{l,t}=0",
        "Cycle-flow KVL; absent when the passive network has no cycles",
        has["branch"] or has["transformer"], "Kirchhoff-Voltage-Law",
    )

    # Device-level constraints.
    add("generator constraints", "dispatch bounds", "-", "g,t", r"\underline p_g\bar P_g\le p_{g,t}\le a_{g,t}\bar P_g", "Continuous generator operating bounds", True, "Generator-fix-p-lower;Generator-fix-p-upper")
    add("generator constraints", "ramp up", "-", "g,t", r"p_{g,t}-p_{g,t-1}\le R_g^{\uparrow}\bar P_g", "Generator ramp-up limit", True, "Generator-p-ramp_limit_up")
    add("generator constraints", "ramp down", "-", "g,t", r"p_{g,t-1}-p_{g,t}\le R_g^{\downarrow}\bar P_g", "Generator ramp-down limit", True, "Generator-p-ramp_limit_down")
    add("generator constraints", "commitment-linked bounds", "-", "g,t", r"\underline p_g\bar P_gu_{g,t}\le p_{g,t}\le a_{g,t}\bar P_gu_{g,t}", "Dispatch linked to binary status", not continuous, "Generator-com-p-lower;Generator-com-p-upper")
    add("generator constraints", "status transition", "-", "g,t", r"u_{g,t}-u_{g,t-1}=v_{g,t}-z_{g,t}", "Startup/shutdown transition", not continuous, "Generator-com-transition-start-up;Generator-com-transition-shut-down")
    add("generator constraints", "minimum up time", "-", "g,t", r"\sum_{\tau=t-T_g^{up}+1}^{t}v_{g,\tau}\le u_{g,t}", "Minimum online duration", not continuous, "Generator-com-up-time")
    add("generator constraints", "minimum down time", "-", "g,t", r"\sum_{\tau=t-T_g^{down}+1}^{t}z_{g,\tau}\le1-u_{g,t}", "Minimum offline duration", not continuous, "Generator-com-down-time")

    add("branch constraints", "line thermal bounds", "-", "l,t", r"-\bar F_l\le f_{l,t}\le\bar F_l", "AC line flow limits", has["branch"], "Line-fix-s-lower;Line-fix-s-upper")
    add("branch constraints", "transformer bounds", "-", "h,t", r"-\bar F_h\le f_{h,t}\le\bar F_h", "Transformer apparent-power limits", has["transformer"], "Transformer-fix-s-lower;Transformer-fix-s-upper")
    add("converter constraints", "link bounds", "-", "k,t", r"\underline p_k\bar P_k\le f_{k,t}\le\bar p_k\bar P_k", "DC branch/converter flow limits", has["link"], "Link-fix-p-lower;Link-fix-p-upper")
    add("converter constraints", "conversion losses", "-", "k,t", r"p^{out}_{k,t}=\eta_k p^{in}_{k,t}", "Efficiency enters nodal injections", has["link"], "Bus-nodal_balance")

    add("storage constraints", "charge bounds", "-", "s,t", r"0\le p^{ch}_{s,t}\le\bar P_s", "Storage charging bound", has["storage"], "StorageUnit-fix-p_store-lower;StorageUnit-fix-p_store-upper")
    add("storage constraints", "discharge bounds", "-", "s,t", r"0\le p^{dis}_{s,t}\le\bar P_s", "Storage discharging bound", has["storage"], "StorageUnit-fix-p_dispatch-lower;StorageUnit-fix-p_dispatch-upper")
    add("storage constraints", "energy bounds", "-", "s,t", r"0\le e_{s,t}\le h_s\bar P_s", "State-of-charge bounds", has["storage"], "StorageUnit-fix-state_of_charge-lower;StorageUnit-fix-state_of_charge-upper")
    add("storage constraints", "energy balance", "-", "s,t", r"e_{s,t}=(1-\lambda_s)^{\Delta t}e_{s,t-1}+\eta_s^{ch}p^{ch}_{s,t}\Delta t-p^{dis}_{s,t}\Delta t/\eta_s^{dis}", "Intertemporal storage balance", has["storage"], "StorageUnit-energy_balance")
    add("storage constraints", "cyclic state of charge", "-", "s", r"e_{s,|\mathcal{T}|}=e_{s,0}", "Enabled per storage parameter", has["storage"], "StorageUnit-energy_balance")

    add("reliability constraints", "load-shedding bound", "-", "n,t", r"0\le p^{shed}_{n,t}\le\bar P_n^{shed}", "Finite high-cost reliability slack", has["shedding"], "Generator-fix-p-lower;Generator-fix-p-upper")
    return rows


def _realized(table: pd.DataFrame, model: object | None) -> pd.Series:
    if model is None:
        return pd.Series(pd.NA, index=table.index, dtype="boolean")
    variables = set(model.variables)
    constraints = set(model.constraints)
    available = variables | constraints
    return table["pypsa_names"].map(
        lambda names: (
            pd.NA if not names else any(name in available for name in names.split(";"))
        )
    ).astype("boolean")
