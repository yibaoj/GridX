"""Shared plotting configuration, labels, and layer dispatch."""

from .api import plot, plot_mapped, plot_standard
from .config import PlotSettings, configure_matplotlib, resolve_plot_settings

__all__ = [
    "PlotSettings",
    "configure_matplotlib",
    "plot",
    "plot_mapped",
    "plot_standard",
    "resolve_plot_settings",
]
