"""Select UC storage formulations by technology subclass."""

from .thermal import add_sensible_heat

_MODELS = {"sensible_heat": add_sensible_heat}


def configure_storage_models(network, case, snapshots, options):
    """Apply the configured model to each subclass, retaining the generic default."""
    models = options.get("models")
    legacy = options.get("model", "storage_unit")
    if models is None:
        if legacy not in {"storage_unit", "sensible_heat"}:
            raise ValueError(f"Unknown storage model: {legacy!r}")
        models = {"sensible_heat": legacy}
    for subclass, name in models.items():
        if name == "storage_unit":
            continue
        if name not in _MODELS:
            raise ValueError(f"Unknown storage model {name!r} for {subclass!r}.")
        assets = case.storage.data.loc[case.storage.data["subclass"].eq(subclass)]
        if not assets.empty:
            _MODELS[name](network, case, snapshots, assets, options)
