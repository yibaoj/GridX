"""Layer-independent plotting API for managers and loaded data objects."""

from __future__ import annotations

from .config import configure_matplotlib, resolve_plot_settings
from .spatial import spatial_background


def plot(data: object, dataset_id: str, **kwargs: object) -> object:
    """Plot one dataset through a consistent layer-independent API."""

    from ..case.model import PowerSystemCase
    from ..mapping.model import MappedData
    from ..standard.model import StandardData

    if isinstance(data, StandardData):
        spatial = kwargs.pop("spatial", data.spatial)
        return plot_standard(
            getattr(data, dataset_id), dataset_id, spatial=spatial, **kwargs
        )
    if isinstance(data, MappedData):
        cells = kwargs.pop("cells", data.spatial)
        spatial = kwargs.pop("spatial", None)
        return plot_mapped(
            getattr(data, dataset_id), dataset_id,
            cells=cells, spatial=spatial, **kwargs,
        )
    if isinstance(data, PowerSystemCase):
        from ..case.plot import plot_case

        options = _plot_options(kwargs)
        return plot_case(data, dataset_id, **options)
    raise TypeError(
        "plot() supports StandardData, MappedData, and PowerSystemCase."
    )


def plot_standard(
    data: object,
    dataset_id: str,
    *,
    spatial: object | None = None,
    **kwargs: object,
) -> object:
    """Plot one already-loaded standard dataset."""

    import matplotlib.pyplot as plt

    from ..standard.plot import PLOTTERS, filter_plot_extent, filter_spatial_levels

    options = _plot_options(kwargs)
    spatial_levels = options.pop("spatial_levels", None)
    if dataset_id == "spatial":
        data = filter_spatial_levels(data, spatial_levels)
    elif dataset_id in {
        "network", "generator", "storage", "load", "population", "resource",
    }:
        spatial = filter_spatial_levels(spatial, spatial_levels)
        if spatial_levels is not None:
            data = filter_plot_extent(data, spatial)
        options["spatial"] = spatial
    with plt.ioff():
        return PLOTTERS[dataset_id](data, **options)


def plot_mapped(
    data: object,
    dataset_id: str,
    *,
    cells: object,
    spatial: object | None = None,
    **kwargs: object,
) -> object:
    """Plot one already-loaded mapped dataset."""

    import matplotlib.pyplot as plt

    from ..mapping.plot import PLOTTERS, filter_plot_levels
    from ..standard.plot import PLOTTERS as STANDARD_PLOTTERS, filter_spatial_levels

    options = _plot_options(kwargs)
    if dataset_id == "parameter":
        with plt.ioff():
            return STANDARD_PLOTTERS["parameter"](data, **options)
    spatial = spatial if spatial is not None else spatial_background(cells)
    spatial_levels = options.pop("spatial_levels", None)
    spatial = filter_spatial_levels(spatial, spatial_levels)
    selected_levels = set(spatial["level"].astype(str))
    if spatial_levels is not None:
        data = filter_plot_levels(data, selected_levels)
    options["spatial"] = spatial
    if dataset_id in {"network", "generator", "storage"}:
        options["cells"] = cells.loc[
            cells["spatial_level"].astype(str).isin(selected_levels)
        ].copy()
    with plt.ioff():
        return PLOTTERS[dataset_id](data, **options)


def _plot_options(kwargs: dict[str, object]) -> dict[str, object]:
    options = dict(kwargs)
    settings = resolve_plot_settings(
        config_path=options.pop("plot_config_path", None),
        language=options.pop("language", None),
        map_crs=options.pop("map_crs", None),
        china_inset=options.pop("china_inset", None),
    )
    configure_matplotlib(settings)
    options.update({
        "language": settings.language,
        "map_crs": settings.map_crs,
        "china_inset": settings.china_inset,
    })
    return options
