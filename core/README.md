# core

Shared datatypes for all Convoy services, published as the `convoy_core`
Python package. Agent specs, plans and revisions, run state and policy,
tool grants, budgets, environment bindings, sandbox job types, subagent
results, events, and land reports live here.

Ownership rule: this package is owned by no service. Services import these
types and never redefine, fork, or privately extend them — one source of
truth for every cross-service schema. Additive changes require review by the
owners of every consuming service.

Pure data only: pydantic models with no I/O, no clients, and no service
logic. Behavior belongs to the service that owns it.
