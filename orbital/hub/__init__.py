"""The Orbital hub (FastAPI). Run it with `python -m orbital.hub`; build it in code with create_app()."""

from orbital.hub.app import Hub, create_app

__all__ = ["Hub", "create_app"]
