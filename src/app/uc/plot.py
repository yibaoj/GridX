"""Colored stacked UC/ED production-simulation plots."""

from __future__ import annotations

from collections.abc import Iterable

import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import pandas as pd

from ...standard.plot import CATEGORY_COLORS
from .model import UnitCommitmentResult


STORAGE_COLORS = {
    "battery": "#8172b3",
    "pumped_hydro": "#5d86ad",
    "compressed_air": "#4c9f9a",
    "thermal_storage": "#a87955",
    "other_storage": "#8a8f8d",
}


def plot_dispatch(
    result: UnitCommitmentResult,
    *,
    start: object = None,
    end: object = None,
    admin_uids: str | Iterable[str] | None = None,
    spatial_uids: str | Iterable[str] | None = None,
    figsize: tuple[float, float] = (12, 6),
    title: str | None = None,
) -> Figure:
    """Plot load, class dispatch, storage charging, and load shedding."""

    prepared = prepare_dispatch_data(
        result,
        start=start,
        end=end,
        admin_uids=admin_uids,
        spatial_uids=spatial_uids,
    )
    snapshots = prepared["snapshots"]
    production = prepared["generation"]
    discharge = prepared["storage_discharge"].rename(
        columns=lambda value: f"{value} discharge"
    )
    charging = -prepared["storage_charge"]
    positive = pd.concat([production, discharge], axis=1)
    positive = positive.T.groupby(level=0).sum().T
    positive = positive.loc[:, positive.sum().gt(0)]
    load = prepared["load"]

    figure, axis = plt.subplots(figsize=figsize)
    colors = [_color(name, index) for index, name in enumerate(positive.columns)]
    if not positive.empty:
        axis.stackplot(
            snapshots, positive.to_numpy().T,
            colors=colors, alpha=0.86, linewidth=0,
        )
    charging_total = charging.sum(axis=1) if not charging.empty else 0.0
    if not charging.empty:
        axis.fill_between(
            snapshots, 0, -charging_total,
            color="#7666a8", alpha=0.55, label="storage charging",
        )
    axis.plot(snapshots, load, color="black", linewidth=1.35, zorder=5)
    axis.plot(
        snapshots, load + charging_total,
        color="#697277", linewidth=0.9, linestyle="--", zorder=5,
    )
    axis.axhline(0, color="#737c77", linewidth=0.55)
    axis.set(
        title=title or "Power-system production simulation",
        xlabel="Time",
        ylabel="Power (MW)",
    )
    axis.grid(axis="y", color="#d9dddb", linewidth=0.45, alpha=0.75)
    handles = [
            *[
                Patch(facecolor=color, label=name)
                for name, color in zip(positive.columns, colors, strict=True)
            ],
            Line2D([], [], color="black", label="load"),
            Line2D(
                [], [], color="#697277", linestyle="--",
                label="load + storage charging",
            ),
        ]
    if not charging.empty:
        handles.insert(
            len(positive.columns),
            Patch(facecolor="#7666a8", alpha=0.55, label="storage charging"),
        )
    axis.legend(
        handles=handles,
        loc="upper left",
        bbox_to_anchor=(1.005, 1),
        frameon=False,
        fontsize=8,
    )
    figure.tight_layout()
    return figure


def prepare_dispatch_data(
    result: UnitCommitmentResult,
    *,
    start: object = None,
    end: object = None,
    admin_uids: str | Iterable[str] | None = None,
    spatial_uids: str | Iterable[str] | None = None,
) -> dict[str, object]:
    """Prepare the class-level series shared by static and web plots."""

    snapshots = result.snapshots
    start = snapshots.min() if start is None else pd.Timestamp(start)
    end = snapshots.max() if end is None else pd.Timestamp(end)
    snapshots = snapshots[(snapshots >= start) & (snapshots <= end)]
    buses = _selected_buses(result, admin_uids, spatial_uids)
    data = result.data.sel(time=snapshots)

    generator_bus = data["generator_bus"].to_series()
    generator = generator_bus.index[generator_bus.isin(buses)]
    dispatch = data["generator_p_mw"].sel(
        generator_uid=generator
    ).to_pandas().fillna(0)
    generator_class = data["generator_carrier"].sel(
        generator_uid=generator
    ).to_series().map(_base_class)
    generation = dispatch.T.groupby(generator_class).sum().T

    storage_bus = data["storage_bus"].to_series()
    storage = storage_bus.index[storage_bus.isin(buses)]
    storage_power = data["storage_p_mw"].sel(
        storage_uid=storage
    ).to_pandas().fillna(0)
    storage_class = data["storage_carrier"].sel(
        storage_uid=storage
    ).to_series().map(_base_class)
    storage_discharge = storage_power.clip(lower=0).T.groupby(storage_class).sum().T
    storage_charge = storage_power.clip(upper=0).T.groupby(storage_class).sum().T

    load_bus = data["load_bus"].to_series()
    load_uid = load_bus.index[load_bus.isin(buses)]
    load = data["load_p_set_mw"].sel(
        load_uid=load_uid
    ).to_pandas().fillna(0).sum(axis=1)
    return {
        "snapshots": snapshots,
        "generation": generation,
        "storage_discharge": storage_discharge,
        "storage_charge": storage_charge,
        "load": load,
    }


def _selected_buses(
    result: UnitCommitmentResult,
    admin_uids: str | Iterable[str] | None,
    spatial_uids: str | Iterable[str] | None,
) -> set[str]:
    buses = result.case.network.bus.data
    selected = pd.Series(True, index=buses.index)
    if admin_uids is not None:
        values = [admin_uids] if isinstance(admin_uids, str) else list(admin_uids)
        selected &= buses["admin_uid"].astype(str).isin(map(str, values))
    if spatial_uids is not None:
        values = [spatial_uids] if isinstance(spatial_uids, str) else list(spatial_uids)
        selected &= buses["spatial_uid"].astype(str).isin(map(str, values))
    result_uids = set(buses.loc[selected, "uid"].astype(str))
    if not result_uids:
        raise ValueError("The requested spatial scope contains no case buses.")
    return result_uids


def _base_class(carrier: object) -> str:
    return str(carrier).split(":", 1)[0]


def _color(name: str, index: int):
    base = name.removesuffix(" discharge")
    if base == "load_shedding":
        return "#c75450"
    if name.endswith(" discharge"):
        return STORAGE_COLORS.get(base, "#8172b3")
    return CATEGORY_COLORS.get(
        base,
        plt.colormaps["tab20"](index % 20),
    )
