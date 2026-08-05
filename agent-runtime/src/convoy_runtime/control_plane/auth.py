"""Auth stub: static bearer token + actor/tenant headers.

Every call carries actor identity which flows into RunEvents. TODO: WorkOS
OIDC at the edge; tenant derived from the verified identity instead of a
header.
"""

from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class Actor:
    actor_id: str
    tenant_id: str


def require_actor(request: Request) -> Actor:
    expected = f"Bearer {request.app.state.config.dev_token}"
    supplied = request.headers.get("authorization")
    if supplied != expected:
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")
    actor_id = request.headers.get("x-actor-id")
    tenant_id = request.headers.get("x-tenant-id")
    if not actor_id or not tenant_id:
        raise HTTPException(
            status_code=400, detail="x-actor-id and x-tenant-id headers are required"
        )
    return Actor(actor_id=actor_id, tenant_id=tenant_id)
