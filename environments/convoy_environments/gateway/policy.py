"""Policy resolution: (environment@version, tool) → connection + effect class.

Fail-closed everywhere: unknown tool, tool not on the allowlist, connection
not active, or manifest drift (the connection's current manifest_hash no
longer matches the hash pinned when the environment version was created) all
deny. Drift denial is what makes hash-pinning real — a connector whose tool
surface changed cannot be reached through an environment certified against
the old surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy import select

from ..db.tables import Connection as ConnectionRow
from ..db.tables import Environment as EnvironmentRow
from ..db.tables import EnvironmentConnection as EnvConnRow
from ..schema import ConnectionManifest, EffectClass, ToolSpec


class PolicyDenied(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class ToolResolution:
    connection: ConnectionRow
    spec: ToolSpec
    effect_class: EffectClass  # after gate_overrides escalation


@dataclass
class EnvironmentSnapshot:
    row: EnvironmentRow
    connections: List[tuple]  # (EnvConnRow, ConnectionRow)


class PolicyEngine:
    def __init__(self, session_factory) -> None:
        self._sf = session_factory

    def load_environment(self, environment_id: str, version: int) -> EnvironmentSnapshot:
        with self._sf() as session:
            env = session.get(EnvironmentRow, (environment_id, version))
            if env is None:
                raise PolicyDenied("unknown environment %s@%d" % (environment_id, version))
            pairs = session.execute(
                select(EnvConnRow, ConnectionRow)
                .join(ConnectionRow, ConnectionRow.id == EnvConnRow.connection_id)
                .where(EnvConnRow.environment_id == environment_id,
                       EnvConnRow.environment_version == version)
            ).all()
        return EnvironmentSnapshot(row=env, connections=[(ec, c) for ec, c in pairs])

    def allowed_tools(self, snapshot: EnvironmentSnapshot,
                      connection_id: Optional[str] = None) -> List[ToolResolution]:
        """Every tool this environment version exposes (drifted or revoked
        connections contribute nothing). `connection_id` scopes to one
        connection — the per-connection MCP doors use this."""
        out: List[ToolResolution] = []
        for ec, conn in snapshot.connections:
            if connection_id is not None and conn.id != connection_id:
                continue
            if conn.status != "active" or conn.manifest_hash != ec.manifest_hash:
                continue
            manifest = ConnectionManifest.model_validate(conn.manifest)
            for name in ec.tool_allowlist or []:
                spec = manifest.tool(name)
                if spec is not None:
                    out.append(ToolResolution(connection=conn, spec=spec,
                                              effect_class=self._effective(ec, spec)))
        return out

    def resolve(self, snapshot: EnvironmentSnapshot, tool: str,
                connection_id: Optional[str] = None) -> ToolResolution:
        for ec, conn in snapshot.connections:
            if connection_id is not None and conn.id != connection_id:
                continue
            if tool not in (ec.tool_allowlist or []):
                continue
            if conn.status != "active":
                raise PolicyDenied("connection %s is %s" % (conn.id, conn.status))
            if conn.manifest_hash != ec.manifest_hash:
                raise PolicyDenied(
                    "manifest drift: connection %s changed since environment version was created" % conn.id
                )
            manifest = ConnectionManifest.model_validate(conn.manifest)
            spec = manifest.tool(tool)
            if spec is None:
                raise PolicyDenied("tool %s is allowlisted but absent from manifest" % tool)
            return ToolResolution(connection=conn, spec=spec, effect_class=self._effective(ec, spec))
        raise PolicyDenied("tool %s is not allowlisted in this environment" % tool)

    @staticmethod
    def _effective(ec: EnvConnRow, spec: ToolSpec) -> EffectClass:
        override: Optional[str] = (ec.gate_overrides or {}).get(spec.name)
        if override == "gated":
            return "gated"  # escalate only — overrides never downgrade
        return spec.effectClass
