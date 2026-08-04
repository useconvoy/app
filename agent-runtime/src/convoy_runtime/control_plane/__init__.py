"""FastAPI control plane — the external (E) API surface (DESIGN.md section 7).

Writes go to Temporal (start_workflow / signals); reads come from Postgres
projections + SSE, never Temporal (CLAUDE.md rule 11).
"""
