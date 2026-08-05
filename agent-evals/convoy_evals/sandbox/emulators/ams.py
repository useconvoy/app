"""AMS emulator — a minimal agency-management-system surface over the `policy`
collection. Reads are pure; update_policy is the one effectful mutation.
"Expiring within N days" is judged against the SIM clock, never wall time.

Port of src/sandbox/emulators/ams.ts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..api import ToolCallCtx, ToolEmulator, WorldStore
from .util import world_clock


class _ListExpiringArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")
    withinDays: int = Field(ge=0)


class _GetPolicyArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")
    policyId: str


class _UpdatePolicyArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")
    policyId: str
    fields: Dict[str, Any]


DAY_MS = 86_400_000


def _parse_ts_ms(v: Any) -> Optional[float]:
    """Parse a date-only or full ISO string into epoch ms; None when invalid."""
    if not isinstance(v, str):
        return None
    s = v + "T00:00:00Z" if len(v) == 10 else v
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() * 1000


def _list_expiring(args: Any, world: WorldStore, ctx: ToolCallCtx) -> Any:
    a = _ListExpiringArgs.model_validate(args)
    now_ms = world_clock(world).now().timestamp() * 1000
    horizon = now_ms + a.withinDays * DAY_MS
    policies = []
    for r in world.list_records("policy"):
        t = _parse_ts_ms(r.fields.get("expiring_date"))
        if t is not None and t <= horizon:
            entry = {"policyId": r.id}
            entry.update(r.fields)
            policies.append(entry)
    return {"policies": policies}


def _get_policy(args: Any, world: WorldStore, ctx: ToolCallCtx) -> Any:
    a = _GetPolicyArgs.model_validate(args)
    record = world.get_record("policy", a.policyId)
    if not record:
        raise ValueError("ams.get_policy: no such policy: %s" % a.policyId)
    # Flattened shape — executors read policy.insured_email etc. directly.
    out: Dict[str, Any] = {"policyId": record.id}
    out.update(record.fields)
    out["updatedAt"] = record.updatedAt
    return out


def _update_policy(args: Any, world: WorldStore, ctx: ToolCallCtx) -> Any:
    a = _UpdatePolicyArgs.model_validate(args)
    if not world.get_record("policy", a.policyId):
        raise ValueError("ams.update_policy: no such policy: %s" % a.policyId)
    record = world.upsert_record("policy", a.policyId, a.fields)
    return {"policyId": record.id, "fields": record.fields, "updatedAt": record.updatedAt}


ams_emulator: List[ToolEmulator] = [
    ToolEmulator(tool="ams.list_expiring", effectful=False, handler=_list_expiring),
    ToolEmulator(tool="ams.get_policy", effectful=False, handler=_get_policy),
    ToolEmulator(tool="ams.update_policy", effectful=True, handler=_update_policy),
]
