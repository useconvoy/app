from __future__ import annotations

import secrets
import threading
import time
from typing import Protocol

from convoy_contracts.execution import (
    IDENTITY_FIELDS,
    PROFILE,
    canonical_digest,
    validate_manifest,
    validate_request,
    validate_result,
    verify_grant,
)
from fastapi import FastAPI, Header, HTTPException, Request


class Runtime(Protocol):
    runtime: str
    artifact_sha256: str

    def get_action(self, observation: list[float]) -> list[float]: ...


def create_app(manifest: dict, runtime: Runtime, *, execution_secret: str, probe_token: str) -> FastAPI:
    validate_manifest(manifest)
    if len(execution_secret.encode()) < 32 or len(probe_token) < 32:
        raise ValueError("configure distinct execution and probe secrets of at least 32 characters")
    if secrets.compare_digest(execution_secret, probe_token):
        raise ValueError("execution and probe secrets must be distinct")
    if manifest["policy"] != {"runtime": runtime.runtime, "artifact_sha256": runtime.artifact_sha256}:
        raise ValueError("loaded runtime/artifact does not match the release manifest")
    digest = canonical_digest(manifest)
    admitted = threading.BoundedSemaphore(1)
    app = FastAPI(title="Convoy inference worker", version="1", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def bounded_body(request: Request, call_next):
        # Check actual streamed bytes, including requests without Content-Length.
        from fastapi.responses import JSONResponse

        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 16384:
                return JSONResponse({"detail": "request too large"}, status_code=413)
        request._body = bytes(body)
        return await call_next(request)

    @app.get("/health")
    def health():
        return {"ready": True, "release_digest": digest, "profile": PROFILE,
                "runtime": runtime.runtime, "artifact_sha256": runtime.artifact_sha256}

    @app.post("/v1/probe")
    def probe(body: dict, authorization: str = Header(default="")):
        if not secrets.compare_digest(authorization, f"Bearer {probe_token}"):
            raise HTTPException(401, "probe credential required")
        if body != {"release_digest": digest, "profile": PROFILE}:
            raise HTTPException(409, "release or profile mismatch")
        return health()

    @app.post("/v1/decisions")
    def decision(body: dict, authorization: str = Header(default="")):
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "execution grant required")
        try:
            grant = verify_grant(authorization[7:], execution_secret)
        except ValueError as error:
            raise HTTPException(401, str(error)) from error
        try:
            validate_request(body)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if body["identity"] != {key: grant[key] for key in IDENTITY_FIELDS}:
            raise HTTPException(403, "request is not authorized by this mission grant")
        if body["identity"]["release_digest"] != digest:
            raise HTTPException(409, "worker serves a different release")
        if body["budget_ms"] > manifest["execution"]["decision_timeout_ms"]:
            raise HTTPException(422, "budget exceeds release limit")
        if not admitted.acquire(blocking=False):
            raise HTTPException(429, "worker busy; no queued decisions", headers={"Retry-After": "1"})
        started = time.monotonic()
        try:
            action = runtime.get_action(body["observation"])
            duration_ms = (time.monotonic() - started) * 1000
            # This is a worker-local admission bound, never a substitute for the
            # robot's arrival-time check in its own monotonic clock domain.
            if duration_ms > body["budget_ms"] or time.time() >= grant["expires_at"]:
                raise HTTPException(504, "inference exceeded budget or mission authorization")
            result = {key: body[key] for key in (
                "identity", "request_id", "observation_id", "sequence", "deadline_monotonic_ns",
            )}
            result.update(action=action, policy_duration_ms=duration_ms)
            validate_result(result)
            return result
        except HTTPException:
            raise
        except Exception as error:
            # Model exceptions may contain paths or data. Expose a stable error;
            # deployment-local logs retain exception type without observations.
            import logging

            logging.getLogger(__name__).error("policy inference failed: %s", type(error).__name__)
            raise HTTPException(502, "runtime failed or returned an invalid action") from error
        finally:
            admitted.release()

    return app
