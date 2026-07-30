# Convoy Labs — Architecture v2: Real Runtime, Environments, Evals, Deploy

Status: design proposal. The POC in this repo proves the governance model
(gateway, policies, approvals, traces, promotion gate). This document
specifies what has to change to make it a real product: an agent runtime that
actually operates against real systems, user-created environments with
attached/enterprise/custom tooling, a sandboxed execution model, a first-class
eval system, and production deployment. It ends with the gap list — things
not in the original ask that will bite if unplanned.

---

## 0. Where the POC actually is (honest baseline)

| Area | POC today | Real product needs |
|---|---|---|
| Runtime | In-process loop inside Next.js; deterministic planner by default, optional Claude API loop | Separate worker fleet, durable queue, real model loop by default, cancellation, budgets |
| Environments | Two seeded, fixed | User-created, full lifecycle, per-env connector instances |
| Connectors | Simulated systems in the app DB (real manifests) | Real MCP-backed connectors + OAuth, bring-your-own MCP, custom tools |
| Credentials | Opaque `vault://` strings | Real vault: KMS envelope encryption, OAuth refresh, rotation |
| Sandbox | None (trust boundary is the gateway API only) | Process → container isolation for the agent loop |
| Evals | 8 hardcoded scenarios, one template, namespaced fixtures in the shared sandbox | User-authored eval sets, hermetic per-task env instances, layered graders, certified promotion |
| Deploy | Flag flip in one process | Pinned immutable releases, trigger infra, canary/rollback, ops |
| Store | JSON file | Postgres + migrations |
| Identity | Hardcoded persona | SSO, RBAC, authenticated approvals |

The single most load-bearing POC decision that carries forward unchanged:
**agents never hold credentials; every action passes the policy gateway.**
Everything below is built around preserving that invariant while making each
side of it real.

---

## 1. Agent runtime (operate in the environment, actually make calls)

### 1.1 Topology

Split the current single process into three services (the spec's §6.1 diagram,
made literal):

```
control plane (Next.js + Postgres)  ← UI, CRUD, approvals, eval orchestration
run queue (Postgres SKIP LOCKED)    ← runs are jobs; workers lease them
runtime workers (Node, N replicas)  ← one sandboxed agent loop per run
policy gateway (its own service)    ← the only thing workers can reach
```

- **Queue**: `runs` table doubles as the queue (`state = queued`, lease with
  `FOR UPDATE SKIP LOCKED`, heartbeat column, lease expiry → re-queue).
  No Redis dependency until scale demands it.
- **Loop**: the Claude tool loop we already have (`runtime/claude.ts`) becomes
  the default, not the seam. Message history persisted per turn (already
  designed for this) means any worker can resume any paused run —
  approval resume, worker crash, and deploy-time drain all reuse one path.
- **Run token, not credentials**: on lease, the worker mints a short-lived JWT
  scoped to `{run_id, deployment_id, exp}`. Every gateway call carries it.
  The gateway resolves run → deployment → environment → policy → credential
  itself. A compromised worker or a prompt-injected agent holds nothing but a
  token that can only do what the policy already allows, attributed to that run.
- **Controls per run**: max turns, max wall-clock, token budget, per-tool call
  count limits. Kill switch must preempt: workers check a cancellation flag on
  every turn AND the gateway rejects calls from killed runs (second check
  already exists today — keep it; defense in depth).

### 1.2 Sandbox — what "sandboxed" needs to mean, in stages

The isolation requirement depends on what the agent can execute:

- **Stage A (tools are gateway JSON calls only — current model):** the agent
  cannot run arbitrary code, so the sandbox's job is credential hygiene and
  blast-radius control. Sufficient: worker child process per run with an empty
  environment (no env vars), seccomp/no-new-privileges, and **egress
  restricted to the gateway URL only** (network namespace or container
  network policy). This is cheap and should ship with the worker split.
- **Stage B (custom code tools, file manipulation, "computer use"):** the
  moment users can attach code as tooling or agents produce/execute code,
  Stage A is not enough. One container per run (gVisor or Firecracker class
  isolation), read-only rootfs, no egress except gateway, CPU/memory/disk
  quotas, per-run scratch volume destroyed at teardown.

Design the worker API so Stage B is a swap of the execution backend, not a
rewrite: the worker contract is `execute(runId) → drives loop via gateway`,
and where that executes (child process vs container) is configuration.

---

## 2. Environments and tooling attachment

### 2.1 Environment lifecycle

Environment CRUD in UI/API (today they are seeded). An environment owns:
connector instances, credentials, policy set, and a **backing type** (see §3 —
this is the eval-critical addition): `live` or `hermetic`.

### 2.2 Three classes of tooling, one gateway contract

Everything mounts behind the gateway as MCP (the spec's §6.2 bet). Attaching
tooling to an environment = registering a tool source + environment-scoped
credentials + per-tool policy.

1. **Catalog connectors (first-party):** HubSpot, Google, Slack, Salesforce…
   Convoy-hosted MCP servers. Attach flow = per-environment OAuth (the
   Sandbox instance authorizes against the sandbox portal, Production against
   the real one). Tokens land in the vault; the gateway owns refresh.
2. **Enterprise / bring-your-own MCP:** register an MCP server URL + auth
   (bearer, OAuth client-credentials, mTLS for on-prem). The gateway calls
   `tools/list` to introspect the manifest. This is how a customer's internal
   platform team exposes their own systems without Convoy writing a connector.
3. **Custom tools (user-defined):**
   - **HTTP tool**: name, description, JSON Schema for args, method + URL
     template, header/body templates referencing vault secrets
     (`{{secret.API_KEY}}`), response field mapping, timeout. Executed by the
     gateway — the secret never leaves it.
   - **OpenAPI import**: upload/point at a spec, select operations to expose
     as tools; generates HTTP tools in bulk.
   - **Code tools** (later, requires sandbox Stage B): user-supplied snippet
     executed in an isolated runner with only declared secrets injected.

### 2.3 Manifest pinning and drift

Tool manifests are captured at attach time and **pinned by hash into every
deployment**. A nightly re-introspection detects drift (schema changed on a
BYO-MCP server, connector API version bump). Drift ⇒ mark affected
deployments "manifest-stale," require eval re-run before the next promotion.
Without this, "the suite was green" silently stops meaning anything.

### 2.4 Vault and health

- Envelope encryption (per-workspace data key wrapped by KMS); secrets are
  write-only through the API; the gateway is the only reader.
- Health checks become real: a read-only probe per connector instance
  (`tools/list` + one cheap read), surfaced on the environment page and
  blocking binding validation exactly like today.

### 2.5 Policy engine extensions

Keep the rule shape; add what enterprises will ask for in week one:
- More condition types: argument matchers (JSONPath + operator), numeric
  thresholds (amount > X), time windows, per-tool rate limits and budgets.
- **Default-deny mode** for production environments (unlisted tool = deny,
  not require-approval).
- Policy versioning: policies snapshot into deployments (see §4) and edits
  produce new versions with an audit trail — policy-as-code follows naturally.

---

## 3. Evals — how the environment must be set up to be evaluated

### 3.1 The core problem: evals are a state problem

A task like "close this deal and check the invoice" requires known starting
state, an isolated blast radius, and a way to read end state. Live systems
give you none of that: a real HubSpot portal is shared, mutable, and
unresettable; two concurrent eval tasks contaminate each other's assertions;
and an eval that emails a real address is an incident.

**Therefore environments get a backing type:**

- **`live`** — real connectors, real side effects. Production and
  staging-against-vendor-sandbox environments.
- **`hermetic`** — Convoy-hosted emulation of the same tool manifests,
  seeded from fixtures, instantiated per eval task, destroyed after grading.

The POC's "simulated systems" stop being demo scaffolding and become the
hermetic backend — same manifests, same gateway, different executor. This is
the most reusable asset in the current codebase.

### 3.2 Fixtures

Three ways to get hermetic state, in order of increasing value:
1. **Hand-authored** (what scenarios do today) — fine for edge cases.
2. **Snapshot from live**: read a live environment through the gateway,
   pass through an **anonymization pipe** (PII mapping, consistent
   pseudonyms so cross-record joins still work), store as a fixture set.
   This is what makes evals representative of the customer's actual data.
3. **Record/replay cassettes**: gateway proxy mode records live
   request/response pairs per tool; replay serves them in hermetic runs.
   Cassettes double as **connector contract tests** — when a replayed
   response no longer matches the live connector's current behavior, that's
   drift detection at the semantic level, not just the schema level (§2.3).

Custom/BYO tools in evals **must** declare a mock: static responses, a
cassette, or "hermetic no-op with canned result." An eval set that can reach
a tool with no mock fails validation at authoring time, not mid-run.

### 3.3 Eval task anatomy

```
EvalTask {
  fixtureRef            // hermetic seed
  trigger               // the event that starts the run
  approvalScript        // REQUIRED: what to do when the run pauses —
                        //   auto-approve-all | auto-reject-all |
                        //   scripted per-gate decisions (approve the discount
                        //   flag, reject the merge), with expected pauses
  graders[]             // layered, see 3.4
  budget                // max turns / tokens / wall-clock
}
```

The `approvalScript` is the piece that is easy to miss: any interesting agent
pauses, so an unattended eval must know how to answer every gate — and
"the run paused at the expected gate with the expected payload" is itself an
assertion (the POC's flag scenarios already grade this; keep that).

### 3.4 Graders, layered

1. **End-state assertions** (deterministic, read through the gateway against
   hermetic state) — what the POC has: CRM field values, doc existence, email
   presence. Never agent self-report.
2. **Trajectory assertions**: tool-call sequence/count constraints, "no
   denied calls," "no more than N turns," "did not touch tool X," args
   constraints on specific calls.
3. **LLM-judge rubrics** for fuzzy outputs (is the invoice email correct,
   professional, numerically consistent with the deal) — judge model pinned
   and versioned, judge prompts stored with the eval set.
4. **Human review queue** for judge-uncertain results; human labels feed back
   into the eval set as golden examples.

### 3.5 What "green" certifies — the certified tuple

An eval result is recorded against the full behavior-determining tuple:

```
(agent_version, model_id, prompt_hash, tool_manifest_hashes, policy_hash, eval_set_version)
```

The promotion gate stops meaning "the suite passed at some point" and starts
meaning "**this exact tuple** is certified, and it equals what you are about
to deploy." Any component changing — model version bump, prompt edit,
manifest drift, policy edit — invalidates certification and triggers
re-evaluation. This is the honest version of the POC's version-pinned gate.

### 3.6 Eval execution

Eval runs are just runs (same queue, same workers, same gateway) with
`triggerType = eval`, pointed at ephemeral hermetic environment instances,
executed at high parallelism (hermetic = embarrassingly parallel), with
pass/fail matrices and score trends per agent over time. Adversarial packs
(§5, gap 4) run in the same harness.

---

## 4. Deploy

### 4.1 A deployment is an immutable release bundle

```
Deployment {
  agent_version, model_id (pinned, not "latest"),
  tool_manifest_hashes, policy_snapshot, params,
  certification_ref     // the eval result tuple that authorized this
}
```

Promotion = create bundle, verify certification matches, activate. Rollback =
activate the previous bundle (instant, because bundles are immutable).

### 4.2 Trigger infrastructure

- **Webhooks**: real subscription provisioning per connector (register the
  HubSpot webhook on attach), signature verification, idempotency keys
  (dedupe redelivery), ordered handling per entity where the source guarantees
  order.
- **Schedules**: cron per deployment with jitter and overlap policy
  (skip / queue / parallel).
- **Manual/API**: what exists today, plus service tokens for customers'
  own systems to trigger runs.
- **Reliability**: retries with backoff for infra failures (never for
  policy rejections), dead-letter queue with a UI, per-agent concurrency
  caps, per-entity mutual exclusion (two runs must not paper the same deal —
  lock on `(agent, entity_key)` derived from the trigger).

### 4.3 Rollout controls

- **Shadow mode**: run the candidate version against live triggers but in a
  hermetic mirror — diff its would-be actions against the incumbent's real
  actions. The safest possible "test in production" for agents, and a natural
  extension of the hermetic machinery.
- **Canary**: route N% of triggers to the new version, auto-rollback on
  failure-rate or gate-rate regression.
- Ops floor: OTel traces across control plane/gateway/workers, alerting on
  run failure rate, approval latency SLO breaches, connector health, stuck
  runs; cost metering (tokens + tool calls) per run/agent/workspace.

---

## 5. Gaps not in the ask (the "what else" list)

1. **Identity, RBAC, authenticated approvals.** There is no user auth at all;
   the approver is a hardcoded persona. Approvals are the security-critical
   surface — they must be authenticated, authorized against the policy's
   approver list, and signed into the audit log. SSO/SCIM is table stakes for
   the buyer this product targets. This is a prerequisite for everything else
   being trustworthy.
2. **Postgres + multi-tenancy.** The JSON store cannot survive a worker
   fleet. Workspace isolation (row-level or schema-level) has to land with
   the migration, not after.
3. **Prompt injection defense.** Agents read CRM notes, emails, docs —
   untrusted input by definition. Mitigations: the credential/gateway
   architecture (already right), tool-output size/content sanitization,
   policy gates as the backstop for consequential actions, and an
   **adversarial eval pack** (injection attempts seeded into fixtures) so
   resistance is measured, not assumed. This is also a differentiating
   investor/security-review story.
4. **Approval UX at scale.** Slack interactive approvals (signed action
   payloads), approve-from-mobile, delegation and escalation chains,
   `timeout → reject` actually enforced (the policy field exists; nothing
   fires it), batch approval, and **approve-with-edits** (the approver fixes
   the email subject line instead of rejecting the whole run — amended args
   recorded on the trace). Approval latency is the product's core SLO;
   this UX is what keeps it low.
5. **Data retention and PII.** Traces contain customer data. Needs retention
   policies, trace redaction rules, encryption at rest, and eventually
   residency. Also: the append-only audit log should become tamper-evident
   (hash-chained) if "prove it to your auditor" is the pitch.
6. **Model lifecycle management.** Pin model versions per deployment;
   provider errors/refusals need fallback behavior; model upgrades go through
   the eval gate like any other change (the certified tuple makes this
   automatic).
7. **Concurrency/consistency on shared entities.** Beyond the per-entity
   lock in §4.2: optimistic concurrency on writes (agent re-reads before
   write; gateway can enforce if-unmodified-since semantics per connector
   where supported).
8. **Agent versioning UX.** The builder only creates v1. Editing → draft →
   new version → eval → promote needs a real flow, plus diff view between
   versions (prompt diff, grant diff).
9. **Run replay and debugging.** The data supports replay (persisted
   messages, full traces); build the UI: step through a run, fork it into a
   hermetic environment ("what would v2 have done here"), which quietly
   reuses the shadow-mode machinery.
10. **Billing/metering.** Per-run metering (spec's pricing sketch) falls out
    of the cost observability in §4.3 — design the events now even if
    billing ships later.
11. **Hermetic/live parity risk.** The eval story leans on emulation being
    faithful. Cassette-based contract tests (§3.2) are the mitigation; treat
    parity as a measured metric per connector, not a hope.

---

## 6. Build sequence

- **M1 — Foundations:** Postgres + migrations, basic auth/workspaces, worker
  split with run queue and run tokens, live model loop as default, Stage A
  sandbox. *(Everything else stacks on this.)*
- **M2 — Tooling:** environment CRUD, vault, catalog OAuth connectors
  (HubSpot first — the demo already tells its story), BYO-MCP registration,
  custom HTTP tools + OpenAPI import, manifest pinning.
- **M3 — Evals:** hermetic backing type + ephemeral instances, fixture
  pipeline (hand-authored + snapshot/anonymize), approval scripts, layered
  graders, certified-tuple promotion gate.
- **M4 — Deploy:** release bundles, webhook/schedule trigger infra, DLQ,
  per-entity locking, rollback, shadow mode, ops/alerting.
- **M5 — Hardening:** Stage B container sandbox, adversarial eval pack,
  Slack interactive approvals + timeout enforcement + approve-with-edits,
  trace redaction/retention.

Sequencing rationale: M1 is a prerequisite for all parallelism; M2 before M3
because evals must mock the tooling classes that exist; M3 before M4 because
deploy's promotion gate is only honest once certification exists; shadow mode
lands in M4 because it reuses M3's hermetic machinery.
