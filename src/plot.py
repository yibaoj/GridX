"""Shared plotting facade for standard, mapped, and case data objects."""

from __future__ import annotations


def plot(data: object, dataset_id: str, **kwargs: object) -> object:
    """Plot one dataset through a consistent layer-independent API."""

    from .case.model import PowerSystemCase
    from .mapping.model import MappedData
    from .standard.model import StandardData

    if isinstance(data, StandardData):
        spatial = kwargs.pop("spatial", data.spatial)
        return plot_standard(
            getattr(data, dataset_id), dataset_id,
            spatial=spatial, **kwargs,
        )
    if isinstance(data, MappedData):
        cells = kwargs.pop("cells", data.spatial)
        spatial = kwargs.pop("spatial", None)
        map_crs = kwargs.pop(
            "map_crs", str(data.config["general"]["metric_crs"])
        )
        return plot_mapped(
            getattr(data, dataset_id), dataset_id,
            cells=cells, spatial=spatial, map_crs=map_crs, **kwargs,
        )
    if isinstance(data, PowerSystemCase):
        from .case.plot import plot_case

        return plot_case(data, dataset_id, **kwargs)
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

    from .standard.plot import PLOTTERS, filter_plot_extent, filter_spatial_levels

    spatial_levels = kwargs.pop("spatial_levels", None)
    if dataset_id == "spatial":
        data = filter_spatial_levels(data, spatial_levels)
    elif dataset_id in {
        "network", "generator", "storage", "load", "population", "resource",
    }:
        spatial = filter_spatial_levels(spatial, spatial_levels)
        if spatial_levels is not None:
            data = filter_plot_extent(data, spatial)
        kwargs["spatial"] = spatial
    with plt.ioff():
        return PLOTTERS[dataset_id](data, **kwargs)


def plot_mapped(
    data: object,
    dataset_id: str,
    *,
    cells: object,
    spatial: object | None = None,
    map_crs: str,
    **kwargs: object,
) -> object:
    """Plot one already-loaded mapped dataset."""

    import matplotlib.pyplot as plt

    from .mapping.plot import PLOTTERS, filter_plot_levels
    from .standard.plot import PLOTTERS as STANDARD_PLOTTERS, filter_spatial_levels

    if dataset_id == "parameter":
        with plt.ioff():
            return STANDARD_PLOTTERS["parameter"](data, **kwargs)
    spatial = spatial if spatial is not None else _spatial_background(cells)
    spatial_levels = kwargs.pop("spatial_levels", None)
    spatial = filter_spatial_levels(spatial, spatial_levels)
    selected_levels = set(spatial["level"].astype(str))
    if spatial_levels is not None:
        data = filter_plot_levels(data, selected_levels)
    kwargs["spatial"] = spatial
    kwargs.setdefault("map_crs", map_crs)
    if dataset_id in {"network", "generator", "storage"}:
        kwargs["cells"] = cells.loc[
            cells["spatial_level"].astype(str).isin(selected_levels)
        ].copy()
    with plt.ioff():
        return PLOTTERS[dataset_id](data, **kwargs)


def _spatial_background(cells: object) -> object:
    """Recover display boundaries from mapped cells without source data."""

    frame = cells[["admin_uid", "spatial_level", "geometry"]].dissolve(
        by=["admin_uid", "spatial_level"], as_index=False
    )
    return frame.rename(columns={
        "admin_uid": "uid", "spatial_level": "level",
    })
