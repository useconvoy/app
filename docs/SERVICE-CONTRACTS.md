# Convoy — Cross-Service Contracts & Assumptions from the Runtime Design

**Place at:** repo root `docs/SERVICE-CONTRACTS.md`. The runtime design (agent-runtime/docs/DESIGN.md, Approved v1) froze contracts and baked in assumptions that constrain every other service. Read the relevant section before designing each one. Anything here you want to change → amend the runtime decision log first, then propagate.

## 0. `core/` — the shared datatypes package (the seventh component)

Every DESIGN §5 type + `RunEvent` envelope + `DeploymentProfile` + `ArtifactRef` live in `core/` at the monorepo root — Python package `convoy_core`, a uv-workspace member consumed by path today and semver-tagged so it can publish cleanly if a service is ever split out. Owned by **no service**, imported by all. The TS client is generated from the control plane's OpenAPI, which uses these types. Policy: additive changes freely; breaking changes require coordinated bumps across services. **No service may fork or privately extend a `core/` type.** `core/` is the first thing M0 scaffolds — every other service's design should assume it exists.

## 1. website/

**Frozen for you:** all mutations via the 8 E endpoints; reads via Postgres projections + SSE — **never Temporal**. SSE envelope: `{event_id, run_id, tenant_id, ts, virtual_ts?, sandbox, type, actor, payload}`; the type catalog is append-only and you must tolerate unknown types. Actor identity (WorkOS) rides every mutation and appears on every event — the audit UI is a projection render, free.
**Assumptions to design around:** ⚠️ **No token streaming in MVP** — the runtime emits events per turn/step, not live model tokens. The console shows turn-granular progress. Live token streaming later = a new streaming channel out of `run_turn`; do not design the MVP console around it. Gate/approval notifications (email/Slack) are yours, triggered off `gate_opened`/`revision_proposed` events. Plan UI must render *versions* (revisions with author/reason/approval), not a single mutable plan. Sandbox runs add two console surfaces: a fast-forward control (`POST /runs/{id}/clock/advance`, virtual-clock runs only) and the `simulated_effects` dry-run review inside the land report.
**Your open questions:** approval UX for mid-run major revisions; gates inbox vs. per-run surfacing; steer composer treatment of note vs. redirect (they are semantically different — make that visible).

## 2. environments/

**Frozen for you:** `EnvironmentBinding` type; bindings are **immutable per version** and pinned per run at start — changing an environment mints a new version, in-flight runs finish on the old one. You own: the registry/resolution API (`environment_id` → binding), data-plane MCP servers behind `connector_endpoints`, sandbox **templates/images/content**, credential-scope definitions (IAM roles + session-policy templates), and the OAuth token vault (KMS-encrypted in stack Postgres; the runtime never reads tokens). The runtime owns `SandboxProvider` *implementations* (Local, ECS); you own what runs inside them.
**Trust obligation (critical):** the runtime *believes* your `ToolGrant.execution` and `side_effecting` flags. A side-effecting tool misflagged as inline breaks retry safety and can double-fire real-world actions. Flag conservatively; the contract tests check shape, not honesty.
**Hard rule inherited:** sandboxes never receive credentials — connectors run in trusted services; data is materialized in, artifacts out.
**Sandbox simulation (yours):** an environment *definition* compiles into two immutable bindings — production (real connectors, real clock) and sandbox (mocks, virtual `ClockConfig`). Mocks must share the `RunClock` (a mock answering in real time inside a virtual-time run breaks world coherence); every `side_effecting` tool must resolve to a mock in sandbox bindings (registry-validated; the runtime's credential minter independently refuses production scopes for `kind=sandbox`); mocks write intents to the `simulated_effects/` outbox instead of acting; generative responses freeze into the scenario artifact on first run so reruns replay a reproducible world. Fidelity ladder to design: fixtures → recorded replay (binding pinning makes production tool traffic recordable) → generative worlds.
**Acceptance bar:** pass `agent-runtime/tests/contracts/` unchanged before the runtime integrates you.
**Your open questions:** binding authoring/versioning UX; per-customer custom environment workflow (it's a paid onboarding deliverable); connector catalog priorities for the audit/GRC vertical.

## 3. agent-evals/

**Frozen for you:** `EvalGate {suite_id, threshold, on_fail}` — the runtime already routes `block/retry/flag`; MVP stubs everything to `flag` writing a Langfuse score, so you can go live without a runtime change. Your inputs are the **S3 archive layout** (below) + plan revision snapshots + `SubagentResult`s + `LandReport`s vs. `success_criteria`, plus Langfuse traces. Graders read archives, never Temporal, and never mutate a run except through the gate result. Rehearsal trajectories arrive flagged `sandbox: true` with dual timestamps — scenario suites *are* eval suites; every sandbox rehearsal is a labeled trajectory.
**Assumption that protects you:** compaction is lossy for context, lossless for the record — full-fidelity transcripts always exist. If you ever find a summary without its archive ref, that's a runtime bug, file it.
**Your open questions:** suite format; grader model policy (must respect per-deployment `approved_models` when running inside a customer stack); replay-against-frozen-binding harness (record/replay of tool traffic — the binding pinning makes this possible).

## 4. telemetry/

**Frozen for you:** OTel is the primitive; span hierarchy run → step → turn → tool with Temporal workflow/activity IDs as attributes. Langfuse is a **sink** (ops account, per-stack projects for MVP; per-stack self-host reserved for customer-VPC mode). Projections are written by a runtime outbox activity — you read and ship, you don't write. RunEvent catalog is append-only.
**Assumptions:** no egress assumptions anywhere — export routes are `DeploymentProfile` data, because customer-VPC mode has none. Logs carry refs, never transcript bodies or secrets.
**Your open questions:** alerting policy (budget_warning, gate SLA breaches, heartbeat gaps); retention per profile; cost dashboards from `TurnResult.cost_usd` roll-ups.

## 5. learning/

**Frozen for you:** inputs are S3 archives + Langfuse scores + Postgres events — batch, offline, never in the run loop for MVP. Sandbox-flagged rehearsal trajectories stay partitioned from production behavior data unless a learning objective explicitly targets rehearsals. **Tombstones are law:** deleted-data events must exclude material from every learning set (compliance property, not a preference). The intended injection point for learned improvements is `AgentSpec.prompt_ref` versioning (and later `RunPolicy` tuning) — improvements re-enter as new versions through the normal approval machinery, never by mutating live runs.
**Your open questions:** improvement-proposal format; eval-gated promotion of new prompt versions (closing the loop with agent-evals/); per-tenant vs. cross-tenant learning boundaries (default: per-tenant until a buyer explicitly consents otherwise).

## 6. S3 archive layout (contract for evals/, learning/, telemetry/)

```
s3://{stack-bucket}/{tenant}/{env}/runs/{run_id}/
  binding.json                     # pinned EnvironmentBinding snapshot
  plans/v{n}.json                  # every revision snapshot
  pinned/{n}.json                  # pinned-header versions
  transcripts/{step_id}/turn-{k}.json
  archives/{step_id}/full.json     # raw pre-compaction record
  summaries/{step_id}.json
  subagents/{child_run_id}/…       # recursive, same shape
  workspace/{snapshot-n}.tar.zst
  simulated_effects/…              # sandbox runs: recorded mock intents (dry-run outbox)
  land/report.json · land/deliverables/
```

Append-only; tombstones supersede, nothing rewrites history.

## 7. Cross-cutting gotchas

Temporal payloads are ciphertext (codec) — nothing outside runtime workers can read them, by design · every DB connection needs tenant context or RLS returns nothing · `ArtifactRef` is the only way blobs move between services · dollars are the budget currency everywhere · all six services deploy per-stack via one Terraform module; shared exceptions are exactly: Temporal Cloud (namespace/stack), WorkOS (edge), ops Langfuse (MVP only).

## 8. Tenancy: organization ≡ tenant (amendment, Aug 5 2026 — pending runtime decision-log entry)

**In plain English:** the console's "organization" and the runtime's "tenant" are the same thing — one customer. (The Aug 5 draft of this section said "workspace ≡ tenant"; that phrasing was superseded by Vinayaka's vocabulary ruling: the deployed website's vocabulary is canonical for user-facing surfaces, so the customer is an **organization**, and console "workspace" now names a different object — what the wire calls an **environment**.) Every service uses the same id for that customer, so their data joins directly everywhere (runs, environments, audit, files) with no translation table. For now, one customer = one organization = one dedicated infrastructure stack; the database *can* hold many organizations, but that capability is reserved for a future shared-SaaS mode and nothing may rely on it yet.

Proposed by environments/, needs Aneesh's sign-off per §0's amendment rule. The table in [docs/LEXICON.md](LEXICON.md) is normative for naming disputes.

- **Naming:** the environments DB table named `workspaces` is a **legacy name for organizations** — the schema does not rename, per the frozen-wire rule. The console API now says `/organizations/{org_id}/…` (branch org-vocabulary). And console "workspace" ≠ this table: a console workspace is what the wire calls an environment (`environment_id`, `EnvironmentBinding`).
- **`environments.workspaces.id` and the runtime's `tenant_id` are the same value — the organization's id.** The binding already asserts this (`EnvironmentBinding.tenant_id` is populated from the organization's row in the legacy `workspaces` table); this section makes it a named contract instead of a coincidence. Every `tenant_id` in RunEvents, RLS predicates, STS session policies, and S3 prefixes (`s3://{stack-bucket}/{tenant}/…`) is an organization id.
- **Dedicated-stack MVP: exactly one real organization per stack.** Stamping a stack and creating its organization are the same provisioning act (the console's organization-creation endpoint — `POST /organizations`, formerly `POST /workspaces` — is provisioning-token-gated for this reason). The environments schema deliberately permits many organizations per database — that is the SaaS-mode option, not an MVP behavior; nothing may assume multi-organization stacks until this section is amended.
- **Identity linkage:** WorkOS is the authority on humans; `environments.users.idp_subject` stores the WorkOS subject, linked on first authenticated console request against an invited email. The website resolves WorkOS session → Convoy user id; services never mint users from tokens.
- **Consequence for every service:** joining runtime data (RunEvents, archives) to control-plane data (connections, environments, grants, audit) is a join on organization id ≡ tenant id. No mapping table exists or should be built.
