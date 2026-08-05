"""FastAPI service layer for the Convoy eval harness.

Usage:
    from convoy_evals.api import create_app
    app = create_app()            # or: uvicorn convoy_evals.api.app:app
"""

from .app import create_app

__all__ = ["create_app"]
