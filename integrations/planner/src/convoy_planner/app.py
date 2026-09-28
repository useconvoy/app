"""Authenticated planner process; the legacy agent remains the sole model owner."""

from __future__ import annotations

import copy
import hashlib
import secrets
import threading
import time

from convoy_contracts.execution import IDENTITY_FIELDS, canonical_digest, validate_identity
from convoy_contracts.pairing import (
    CONTROLLED_PLANNER_RUNTIME,
    PAIRED_PROFILE,
    PLAN_ECHO_FIELDS,
    validate_plan_request,
    validate_plan_result,
    validate_release_manifest,
    verify_planner_grant,
)
from fastapi import FastAPI, Header, HTTPException, Request

from .artifact import artifact_descriptor, implementation_sources
from .sessions import Sessions


def create_app(manifest: dict, backend, *, execution_secret: str, probe_token: str) -> FastAPI:
    manifest = copy.deepcopy(manifest)
    validate_release_manifest(manifest)
    if manifest["profile"] != PAIRED_PROFILE:
        raise ValueError("planner requires paired manifest")
    if (len(execution_secret.encode()) < 32 or len(probe_token) < 32 or
            secrets.compare_digest(execution_secret, probe_token)):
        raise ValueError("distinct planner execution and probe secrets of at least 32 characters required")
    # Capture this installed implementation at process creation. Inference never reads
    # possibly replaced source files or mutable manifests to label an admitted result.
    sources = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in implementation_sources().items()}
    incarnation = secrets.token_hex(16)
    digest = canonical_digest(manifest)
    admitted = threading.BoundedSemaphore(1)
    sessions = Sessions()
    app = FastAPI(title="Convoy fixed-task planner", version="1", docs_url=None, redoc_url=None)

    def snapshot():
        identity = backend.inspect()
        if manifest["planner"]["runtime"] == CONTROLLED_PLANNER_RUNTIME:
            from .controlled import ControlledBackend, controlled_descriptor, validate_controlled_identity

            if not isinstance(backend, ControlledBackend):
                raise ValueError("controlled profile requires its explicit controlled backend")
            validate_controlled_identity(identity)
            descriptor = controlled_descriptor(fingerprints=sources)
            backend_incarnation = identity["backend_incarnation"]
        else:
            descriptor = artifact_descriptor(identity, fingerprints=sources)
            backend_incarnation = identity["gateway_incarnation"]
        artifact = canonical_digest(descriptor)
        if artifact != manifest["planner"]["artifact_sha256"]:
            raise HTTPException(409, "loaded planner artifact differs from bundle")
        return identity, {"planner_artifact_sha256": artifact,
                          "planner_incarnation": canonical_digest([incarnation, backend_incarnation]),
                          "runtime_generation": identity["runtime_generation"]}

    @app.middleware("http")
    async def bounded_body(request: Request, call_next):
        from fastapi.responses import JSONResponse

        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 16384:
                return JSONResponse({"detail": "planner request exceeds bound"}, status_code=413)
            body.extend(chunk)
        request._body = bytes(body)
        return await call_next(request)

    @app.get("/health")
    def health():
        return {"service": "planner", "release_digest": digest, "profile": PAIRED_PROFILE}

    @app.post("/v1/probe")
    def probe(body: dict, authorization: str = Header(default="")):
        if not secrets.compare_digest(authorization, "Bearer " + probe_token):
            raise HTTPException(401, "planner probe credential required")
        if body != {"release_digest": digest, "profile": PAIRED_PROFILE}:
            raise HTTPException(409, "planner bundle mismatch")
        try:
            _, identity = snapshot()
            return {"ready": True, "release_digest": digest, "profile": PAIRED_PROFILE,
                    "runtime": manifest["planner"]["runtime"], **identity}
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(503, "planner gateway identity unavailable") from error

    def authorize(body: dict, authorization: str, *, plan=False):
        try:
            if not authorization.startswith("Bearer "):
                raise ValueError("planner grant required")
            grant = verify_planner_grant(authorization[7:], execution_secret)
        except ValueError as error:
            raise HTTPException(401, str(error)) from error
        try:
            if plan:
                validate_plan_request(body)
            else:
                if set(body) != {"identity"}:
                    raise ValueError("planner session requires exactly identity")
                validate_identity(body["identity"])
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if body["identity"] != {key: grant[key] for key in IDENTITY_FIELDS}:
            raise HTTPException(403, "planner grant identity mismatch")
        if body["identity"]["release_digest"] != digest:
            raise HTTPException(409, "planner serves another bundle")
        return grant

    @app.post("/v1/sessions/start")
    def start(body: dict, authorization: str = Header(default="")):
        authorize(body, authorization)
        if not admitted.acquire(blocking=False):
            raise HTTPException(429, "planner busy; no queued session starts")
        session = None
        try:
            grant = authorize(body, authorization)
            session, fresh = sessions.start(grant)
            if fresh:
                session.gateway_identity, session.planner_identity = snapshot()
            sessions.check(session)
            return {"identity": body["identity"], "next_sequence": session.next_sequence, **session.planner_identity}
        except HTTPException:
            if session:
                sessions.poison(session)
            raise
        except Exception as error:
            if session:
                sessions.poison(session)
            raise HTTPException(503, "planner gateway unavailable") from error
        finally:
            admitted.release()

    @app.post("/v1/sessions/end")
    def end(body: dict, authorization: str = Header(default="")):
        sessions.close(authorize(body, authorization))
        return {"identity": body["identity"], "closed": True}

    @app.post("/v1/plans")
    def plan(body: dict, authorization: str = Header(default="")):
        authorize(body, authorization, plan=True)
        if body["budget_ms"] > manifest["planning"]["timeout_ms"]:
            raise HTTPException(422, "planner budget exceeds release limit")
        if not admitted.acquire(blocking=False):
            raise HTTPException(429, "planner busy; no queued plans")
        started = time.monotonic()
        session = None
        try:
            grant = authorize(body, authorization, plan=True)
            session = sessions.reserve(grant)
            remaining = min(body["budget_ms"] / 1000 - (time.monotonic() - started),
                            grant["expires_at"] - time.time())
            if remaining <= 0:
                raise HTTPException(504, "planner deadline expired before model admission")
            decision = backend.propose(session.gateway_identity, remaining)
            duration = (time.monotonic() - started) * 1000
            sessions.check(session)
            if duration > body["budget_ms"] or time.time() >= grant["expires_at"]:
                raise HTTPException(504, "planner exceeded original budget or mission grant")
            return validate_plan_result({**{key: body[key] for key in PLAN_ECHO_FIELDS},
                **session.planner_identity, "decision": decision, "planner_duration_ms": duration})
        except HTTPException:
            if session:
                sessions.poison(session)
            raise
        except Exception as error:
            if session:
                sessions.poison(session)
            # Do not expose model text, task content or backend exception payloads.
            raise HTTPException(502, "planner unavailable, changed identity, or returned invalid output") from error
        finally:
            admitted.release()

    return app
