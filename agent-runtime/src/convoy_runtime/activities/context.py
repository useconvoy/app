"""assemble_pinned_header activity.

Builds the turn's pinned header from `RunState` (pure function) and archives
it to the artifact store; only the ref rides back through Temporal. The
workflow calls this before every turn so the header always reflects current
plan, budget, and steer state.
"""

from temporalio import activity

from convoy_core import ArtifactRef, RunState
from convoy_runtime.activities import names
from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.context_assembly import build_pinned_header


class ContextActivities:
    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    @activity.defn(name=names.ASSEMBLE_PINNED_HEADER)
    async def assemble_pinned_header(self, state: RunState) -> ArtifactRef:
        header = build_pinned_header(state)
        key = f"runs/{state.run_id}/pinned/turn-{state.turn_count + 1}.json"
        return await self._store.put_json(key, header)
