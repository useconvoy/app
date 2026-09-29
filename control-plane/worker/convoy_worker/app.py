from __future__ import annotations

import secrets
import threading
import time
from typing import Protocol

from convoy_contracts.execution import (
    IDENTITY_FIELDS,
    MAX_VISUAL_REQUEST_BYTES,
    VISUAL_PROFILE,
    canonical_digest,
    validate_identity,
    validate_request,
    validate_result,
    verify_grant,
)
from convoy_contracts.grants import GrantVerifier
from convoy_contracts.pairing import action_manifest, validate_release_manifest
from fastapi import FastAPI, Header, HTTPException, Request

from .sessions import Sessions


class Runtime(Protocol):
    runtime: str
    artifact_sha256: str

    def get_action(self, observation: list[float] | dict) -> list[float]: ...


def create_app(manifest: dict, runtime: Runtime, *, execution_secret: str | None = None,
               grant_verifier: GrantVerifier | None = None, probe_token: str) -> FastAPI:
    validate_release_manifest(manifest)
    policy_manifest = action_manifest(manifest)
    if (execution_secret is None) == (grant_verifier is None):
        raise ValueError("configure exactly one execution authorization mode")
    if len(probe_token) < 32:
        raise ValueError("configure a probe secret of at least 32 characters")
    if execution_secret is not None:
        if len(execution_secret.encode()) < 32 or secrets.compare_digest(execution_secret, probe_token):
            raise ValueError("configure distinct execution and probe secrets of at least 32 characters")
    elif not isinstance(grant_verifier, GrantVerifier) or grant_verifier.purpose != "action":
        raise ValueError("worker requires an action-purpose verifier")
    if policy_manifest["policy"] != {"runtime": runtime.runtime, "artifact_sha256": runtime.artifact_sha256}:
        raise ValueError("loaded runtime/artifact does not match the release manifest")
    visual = policy_manifest["profile"] == VISUAL_PROFILE
    if visual and (getattr(runtime, "profile", None) != VISUAL_PROFILE or
                   not callable(getattr(runtime, "reset_session", None))):
        raise ValueError("visual runtime must declare its profile and implement per-mission reset")
    sessions = Sessions()
    digest = canonical_digest(manifest)
    admitted = threading.BoundedSemaphore(1)
    app = FastAPI(title="Convoy inference worker", version="1", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def bounded_body(request: Request, call_next):
        # Check actual streamed bytes, including requests without Content-Length.
        from fastapi.responses import JSONResponse

        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > (MAX_VISUAL_REQUEST_BYTES if visual else 16384):
                return JSONResponse({"detail": "request too large"}, status_code=413)
            body.extend(chunk)
        request._body = bytes(body)
        return await call_next(request)

    @app.get("/health")
    def health():
        return {"ready": True, "release_digest": digest, "profile": manifest["profile"],
                "runtime": runtime.runtime, "artifact_sha256": runtime.artifact_sha256}

    @app.post("/v1/probe")
    def probe(body: dict, authorization: str = Header(default="")):
        if not secrets.compare_digest(authorization, f"Bearer {probe_token}"):
            raise HTTPException(401, "probe credential required")
        if body != {"release_digest": digest, "profile": manifest["profile"]}:
            raise HTTPException(409, "release or profile mismatch")
        return health()

    def verify_token(token: str):
        # A configured asymmetric verifier never falls back to a shared secret.
        return grant_verifier.verify(token) if grant_verifier is not None else verify_grant(token, execution_secret)

    def authorize(body: dict, authorization: str, *, decision: bool = False):
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "execution grant required")
        try:
            grant = verify_token(authorization[7:])
        except ValueError as error:
            raise HTTPException(401, str(error)) from error
        try:
            if decision:
                validate_request(body, policy_manifest["profile"])
            else:
                if set(body) != {"identity"}:
                    raise ValueError("session requires exactly identity")
                validate_identity(body["identity"])
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if body["identity"] != {key: grant[key] for key in IDENTITY_FIELDS}:
            raise HTTPException(403, "request is not authorized by this mission grant")
        if body["identity"]["release_digest"] != digest:
            raise HTTPException(409, "worker serves a different release")
        return grant

    def recheck_grant(authorization: str):
        try:
            return verify_token(authorization[7:])
        except ValueError as error:
            raise HTTPException(401, str(error)) from error

    @app.post("/v1/sessions/start")
    def start_session(body: dict, authorization: str = Header(default="")):
        if not visual:
            raise HTTPException(404, "this profile is stateless")
        authorize(body, authorization)
        if not admitted.acquire(blocking=False):
            raise HTTPException(429, "worker busy; no queued session resets")
        session = None
        try:
            grant = recheck_grant(authorization)
            session, fresh = sessions.start(grant)
            if fresh:
                runtime.reset_session(body["identity"])
            recheck_grant(authorization)
            sessions.check(session)
            return {"identity": body["identity"], "next_sequence": session.next_sequence}
        except Exception:
            if session is not None:
                sessions.poison(session)
            raise
        finally:
            admitted.release()

    @app.post("/v1/sessions/end")
    def end_session(body: dict, authorization: str = Header(default="")):
        if not visual:
            raise HTTPException(404, "this profile is stateless")
        grant = authorize(body, authorization)
        sessions.close(grant)
        return {"identity": body["identity"], "closed": True}

    @app.post("/v1/decisions")
    def decision(body: dict, authorization: str = Header(default="")):
        grant = authorize(body, authorization, decision=True)
        if body["budget_ms"] > policy_manifest["execution"]["decision_timeout_ms"]:
            raise HTTPException(422, "budget exceeds release limit")
        if not admitted.acquire(blocking=False):
            raise HTTPException(429, "worker busy; no queued decisions", headers={"Retry-After": "1"})
        started = time.monotonic()
        session = None
        try:
            grant = recheck_grant(authorization)
            if visual:
                session = sessions.reserve(grant, body, policy_manifest["execution"]["max_steps"])
            action = runtime.get_action(body["observation"])
            duration_ms = (time.monotonic() - started) * 1000
            # This is a worker-local admission bound, never a substitute for the
            # robot's arrival-time check in its own monotonic clock domain.
            if duration_ms > body["budget_ms"] or time.time() >= grant["expires_at"]:
                raise HTTPException(504, "inference exceeded budget or mission authorization")
            if session is not None:
                sessions.check(session)
            result = {key: body[key] for key in (
                "identity", "request_id", "observation_id", "sequence", "deadline_monotonic_ns",
            )}
            result.update(action=action, policy_duration_ms=duration_ms)
            validate_result(result)
            recheck_grant(authorization)
            return result
        except HTTPException:
            if session is not None:
                sessions.poison(session)
            raise
        except Exception as error:
            if session is not None:
                sessions.poison(session)
            # Model exceptions may contain paths or data. Expose a stable error;
            # deployment-local logs retain exception type without observations.
            import logging

            logging.getLogger(__name__).error("policy inference failed: %s", type(error).__name__)
            raise HTTPException(502, "runtime failed or returned an invalid action") from error
        finally:
            admitted.release()

    return app
