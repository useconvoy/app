"""Per-run gateway tokens.

The runtime mints one token per run (via the internal API) and hands it to
the devbox. It is scoped to (run, mission, environment@version, workspace)
with a short expiry renewed per step lease — useless anywhere but this
gateway. The signing secret never enters a devbox.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

_ALG = "HS256"
_ISSUER = "convoy-gateway"


class TokenError(Exception):
    pass


@dataclass
class RunClaims:
    run_id: str
    mission_id: str
    workspace_id: str
    environment_id: str
    environment_version: int


def signing_secret() -> str:
    secret = os.environ.get("CONVOY_GATEWAY_SECRET", "")
    if not secret:
        raise TokenError("CONVOY_GATEWAY_SECRET is not set")
    return secret


def mint_run_token(claims: RunClaims, ttl_s: int = 3600, secret: Optional[str] = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "iss": _ISSUER,
        "sub": claims.run_id,
        "missionId": claims.mission_id,
        "workspaceId": claims.workspace_id,
        "environmentId": claims.environment_id,
        "environmentVersion": claims.environment_version,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_s),
    }
    return jwt.encode(payload, secret or signing_secret(), algorithm=_ALG)


def verify_run_token(token: str, secret: Optional[str] = None) -> RunClaims:
    try:
        payload = jwt.decode(token, secret or signing_secret(), algorithms=[_ALG], issuer=_ISSUER)
    except jwt.PyJWTError as err:
        raise TokenError("invalid run token: %s" % err)
    return RunClaims(
        run_id=payload["sub"],
        mission_id=payload["missionId"],
        workspace_id=payload["workspaceId"],
        environment_id=payload["environmentId"],
        environment_version=int(payload["environmentVersion"]),
    )
