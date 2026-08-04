"""Stub environments/ implementation for tests.

The binding fixtures used by test layers, mirroring what the compose stub-env
container serves (agent-runtime/docker/stub-env/server.py), plus an
in-process side-effect journal with the same key semantics the container
enforces: a repeated idempotency key never fires the effect twice — it
returns the recorded result. The fast lanes assert against this journal;
the chaos lane asserts against the container's.
"""

from dataclasses import dataclass, field
from typing import Any

from convoy_core import ClockConfig, EnvironmentBinding


def stub_binding(
    environment_id: str = "stub-local",
    tenant_id: str = "tenant-test",
    *,
    kind: str = "sandbox",
    clock: ClockConfig | None = None,
) -> EnvironmentBinding:
    return EnvironmentBinding(
        id=environment_id,
        tenant_id=tenant_id,
        kind="production" if kind == "production" else "sandbox",
        tool_registry=[],
        connector_endpoints={},
        credential_scope="stub:no-credentials",
        data_namespace=f"{tenant_id}/stub",
        sandbox_template="stub",
        clock=clock or ClockConfig(),
    )


@dataclass
class JournalRecord:
    idempotency_key: str
    tool_id: str
    args: dict[str, Any]
    result: dict[str, Any]
    executions: int = 1
    requests: int = 1


@dataclass
class SideEffectJournal:
    """In-memory twin of the stub-env container's journal."""

    records: dict[str, JournalRecord] = field(default_factory=lambda: {})

    def record(
        self, tool_id: str, idempotency_key: str, args: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        """Execute-or-replay one side effect. Returns (result, replayed)."""
        existing = self.records.get(idempotency_key)
        if existing is not None:
            existing.requests += 1
            return existing.result, True
        result: dict[str, Any] = {"ok": True, "tool_id": tool_id, "args": args}
        self.records[idempotency_key] = JournalRecord(
            idempotency_key=idempotency_key, tool_id=tool_id, args=args, result=result
        )
        return result, False

    def executions(self, idempotency_key: str) -> int:
        record = self.records.get(idempotency_key)
        return record.executions if record else 0

    def requests(self, idempotency_key: str) -> int:
        record = self.records.get(idempotency_key)
        return record.requests if record else 0
