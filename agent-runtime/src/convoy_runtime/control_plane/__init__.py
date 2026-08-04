"""FastAPI control plane — the external API surface.

Writes go to Temporal (start_workflow / signals); reads come from Postgres
projections + SSE, never Temporal — user traffic must not reach the workers'
orchestration store.
"""
