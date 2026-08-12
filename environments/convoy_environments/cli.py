"""Founder CLI — the white-glove setup surface until the console wizard exists."""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="convoy-environments")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("generate-master-key", help="Generate a CONVOY_MASTER_KEY value")
    init_db = sub.add_parser("init-db", help="Create all tables (dev; use alembic in prod)")
    init_db.add_argument("--url", default="", help="Database URL (defaults to CONVOY_DATABASE_URL)")
    serve = sub.add_parser("serve", help="Run gateway + console API on one port")
    serve.add_argument("--port", type=int, default=8780)

    args = parser.parse_args()
    if args.command == "generate-master-key":
        from .secrets.builtin import MasterKey

        print(MasterKey.generate())
    elif args.command == "init-db":
        from .db import Base, make_engine

        engine = make_engine(args.url)
        Base.metadata.create_all(engine)
        print("created tables on %s" % (engine.url,))  # URL is a NamedTuple — wrap it
    elif args.command == "serve":
        import os

        import uvicorn
        from fastapi import FastAPI

        from .console_api import build_console_app
        from .db import SqlEventLog, make_engine, make_session_factory
        from .gateway import GatewayService
        from .gateway.hooks import HookDispatcher
        from .gateway.mcp_server import build_app as build_gateway_app
        from .secrets import BuiltinBackend, MasterKey, SecretsService

        session_factory = make_session_factory(make_engine())
        secrets = SecretsService(session_factory, {"builtin": BuiltinBackend(MasterKey())})
        log = SqlEventLog(session_factory)
        hook_dispatcher = HookDispatcher(
            session_factory,
            control_plane_url=os.environ.get("CONVOY_CONTROL_PLANE_URL", "http://localhost:8700"),
            control_plane_token=os.environ.get("CONVOY_CONTROL_PLANE_TOKEN", ""),
        )
        app = FastAPI(title="convoy-environments")
        app.mount(
            "/gateway",
            build_gateway_app(
                GatewayService(
                    session_factory,
                    secrets,
                    event_log=log,
                    sandbox_url=os.environ.get("CONVOY_CONNECTOR_SANDBOX_URL", ""),
                    sandbox_admin_token=os.environ.get("SANDBOX_ADMIN_TOKEN", ""),
                ),
                hook_dispatcher=hook_dispatcher,
            ),
        )
        app.mount("/console", build_console_app(session_factory, secrets))
        uvicorn.run(app, host="0.0.0.0", port=args.port)
    else:
        parser.print_help()
        sys.exit(1)
