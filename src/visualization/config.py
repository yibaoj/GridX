"""Configuration shared by all static plotting layers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
import tomllib


DEFAULT_PLOT_CONFIG = Path(__file__).resolve().parents[2] / "config/plot.toml"
_CJK_FONT_CANDIDATES = (
    "PingFang SC",
    "Hiragino Sans GB",
    "Arial Unicode MS",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "Microsoft YaHei",
    "SimHei",
    "WenQuanYi Zen Hei",
)


@dataclass(frozen=True)
class PlotSettings:
    """Resolved display options applied consistently across all layers."""

    language: str
    map_crs: str
    china_inset: bool
    font_family_zh: str
    font_family_en: str


def resolve_plot_settings(
    *,
    config_path: str | Path | None = None,
    language: str | None = None,
    map_crs: str | None = None,
    china_inset: bool | None = None,
) -> PlotSettings:
    """Load defaults and apply explicit per-call overrides."""

    path = DEFAULT_PLOT_CONFIG if config_path is None else Path(config_path)
    if not path.is_absolute():
        path = DEFAULT_PLOT_CONFIG.parents[1] / path
    with path.expanduser().resolve().open("rb") as file:
        values = tomllib.load(file)["general"]
    settings = PlotSettings(
        language=str(values["language"]),
        map_crs=str(values["map_crs"]),
        china_inset=bool(values["china_inset"]),
        font_family_zh=str(values.get("font_family_zh", "")),
        font_family_en=str(values.get("font_family_en", "DejaVu Sans")),
    )
    settings = replace(
        settings,
        language=settings.language if language is None else str(language),
        map_crs=settings.map_crs if map_crs is None else str(map_crs),
        china_inset=(
            settings.china_inset if china_inset is None else bool(china_inset)
        ),
    )
    if settings.language not in {"zh", "en"}:
        raise ValueError("plot language must be 'zh' or 'en'.")
    return settings


def configure_matplotlib(settings: PlotSettings | None = None) -> str:
    """Apply the language-appropriate installed font and return its name."""

    from matplotlib import rcParams

    settings = settings or resolve_plot_settings()
    requested = (
        settings.font_family_zh
        if settings.language == "zh"
        else settings.font_family_en
    )
    available = _available_fonts()
    if requested:
        if requested not in available:
            raise ValueError(f"Configured plot font is not installed: {requested}")
        selected = requested
    elif settings.language == "zh":
        selected = next(
            (name for name in _CJK_FONT_CANDIDATES if name in available),
            "DejaVu Sans",
        )
    else:
        selected = "DejaVu Sans"
    rcParams["font.family"] = ["sans-serif"]
    rcParams["font.sans-serif"] = [selected, "DejaVu Sans"]
    rcParams["axes.unicode_minus"] = False
    return selected


@lru_cache(maxsize=1)
def _available_fonts() -> frozenset[str]:
    from matplotlib import font_manager

    return frozenset(font.name for font in font_manager.fontManager.ttflist)
