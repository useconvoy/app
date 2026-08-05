"""convoy_environments — the bridge between agent runtimes and the real world.

Aneesh's runtime (devbox on E2B) talks to this layer over exactly two paths:
the governed tool gateway (MCP, terminated here) and the browser egress proxy.
Everything else — connections, environment policy, secrets, RBAC, gates —
lives behind those two doors. Agents never hold credentials; the gateway and
the fill sidecar inject them outside model context.
"""

__version__ = "0.1.0"
