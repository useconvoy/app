from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Settings, get_settings, set_settings
from .db import db_status, init_engine, session_scope
from .services.bootstrap import bootstrap

log = logging.getLogger("convoy.app")


def create_app(settings: Settings | None = None, *, start_scheduler: bool | None = None) -> FastAPI:
    if settings is not None:
        set_settings(settings)
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    init_engine(settings)
    with session_scope() as db:
        bootstrap(db, settings)

    sched = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal sched
        if start_scheduler if start_scheduler is not None else settings.scheduler_inprocess:
            from .services.worker import WorkerThread

            sched = WorkerThread()
            sched.start()
        yield
        if sched:
            sched.stop()

    app = FastAPI(
        title="Convoy",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    from .chat_body_limit import ChatBodyLimit
    from .routers import auth, enrollment, users

    app.add_middleware(ChatBodyLimit)

    for r in (auth.router, users.router, enrollment.router):
        app.include_router(r)
    from .routers import (
        evaluations,
        offline_evaluations,
        platform,
        robot_qualification,
        robot_registry,
        workspace_documents,
    )

    app.include_router(platform.router)
    app.include_router(robot_registry.router)
    app.include_router(robot_qualification.router)
    app.include_router(evaluations.router)
    app.include_router(workspace_documents.router)
    app.include_router(offline_evaluations.router)
    _include_optional(app)

    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException):
        return JSONResponse(
            {"error": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None)
        )

    from fastapi.exceptions import RequestValidationError

    from .db import WriteConflict

    @app.exception_handler(WriteConflict)
    async def _write_conflict(request: Request, exc: WriteConflict):
        return JSONResponse(
            {"error": "database busy; retry the complete request with the same idempotency key"},
            status_code=503,
            headers={"Retry-After": "1"},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request: Request, exc: RequestValidationError):
        errs = exc.errors()

        def where(loc) -> str:
            # Drop FastAPI's leading "body" source marker but keep fields that are themselves named body.
            parts = list(loc)
            return ".".join(str(x) for x in (parts[1:] if parts[:1] == ["body"] else parts))

        summary = "; ".join(f"{where(e.get('loc', []))}: {e.get('msg')}" for e in errs[:5])
        safe = [
            {"loc": [str(x) for x in e.get("loc", [])], "msg": str(e.get("msg")), "type": str(e.get("type"))}
            for e in errs[:20]
        ]
        return JSONResponse({"error": f"invalid request: {summary}", "detail": safe}, status_code=422)

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__, "simulator": settings.simulator, "db": db_status()}

    dist = settings.web_dist or _default_web_dist()
    if dist and (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "not found")
            candidate = dist / path
            if path and candidate.is_file() and candidate.resolve().is_relative_to(dist.resolve()):
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


def _include_optional(app: FastAPI) -> None:
    """Routers added by later work packages register themselves here as they land."""
    import importlib

    for name in (
        "devices",
        "chat",
        "catalog",
        "agent",
        "agent_catalog",
        "operations",
        "rollouts",
        "schedules",
        "evidence",
        "admin",
        "sim",
    ):
        try:
            mod = importlib.import_module(f"convoy_server.routers.{name}")
        except ModuleNotFoundError:
            continue
        app.include_router(mod.router)


def _default_web_dist() -> Path | None:
    here = Path(__file__).resolve()
    for cand in (here.parent / "web_dist", here.parents[2] / "web" / "dist"):
        if (cand / "index.html").exists():
            return cand
    return None
