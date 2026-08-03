"""convoy_core — the co-signed contracts every Convoy workstream builds against.

`events` is the append-only per-mission event taxonomy; `ports` is the runtime
seam. Both are the constitution: changes need both founders' review, and must
stay wire-compatible with logs already recorded by the TS harness and the
Python eval harness (camelCase field names, exclude_none serialization).
"""

from . import events, ports

__all__ = ["events", "ports"]
