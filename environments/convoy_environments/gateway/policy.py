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
from ..schema import ConnectionManifest, ToolSpec


class PolicyDenied(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class ToolResolution:
    connection: ConnectionRow
    spec: ToolSpec
    execution: str  # inline | promoted, after promote_overrides escalation
    side_effecting: bool


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
                    out.append(self._resolution(ec, conn, spec))
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
            return self._resolution(ec, conn, spec)
        raise PolicyDenied("tool %s is not allowlisted in this environment" % tool)

    @staticmethod
    def _resolution(ec: EnvConnRow, conn: ConnectionRow, spec: ToolSpec) -> ToolResolution:
        """promote_overrides escalate to promoted + side-effecting; overrides
        never relax a manifest annotation."""
        promoted = spec.name in (ec.promote_overrides or [])
        return ToolResolution(
            connection=conn, spec=spec,
            execution="promoted" if promoted else spec.execution,
            side_effecting=True if promoted else spec.sideEffecting,
        )
