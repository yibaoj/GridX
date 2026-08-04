"""Lightweight web interface for mapped power-system data."""

from .data import WebDataService

__all__ = ["WebDataService", "serve"]


def serve(*args: object, **kwargs: object) -> None:
    """Start the web server without importing it during package discovery."""

    from .app import serve as run

    run(*args, **kwargs)
