# Milestone status: what is built, what is missing, what needs improving

Audited against `main @ 44bbfba`. Every claim below was checked against the code, not inferred
from the design docs. Milestones are the M1–M5 sequence from `docs/architecture-v2.md` §6.

Read `HANDOFF.md` first for orientation (three execution stacks, two data stores, three auth
modes). This document is narrower: it scores the roadmap and orders the remaining work.

---

## 0. Scorecard

| Milestone | Theme | State | One-line summary |
|---|---|---|---|
| **M0** | Governance POC | **Done** | Gateway, policies, approvals, traces, promotion gate, kill switch all work end to end |
| **M1** | Foundations (Postgres, identity, workers, vault) | **~20%** | Control-plane Postgres exists; demo store, workers, run tokens, and vault do not. **Approval identity is unauthenticated and self-asserted.** |
| **M2** | Tooling (environments, connectors, custom tools) | **~5%** | Connectors are 5 hardcoded simulations. No environment CRUD, no OAuth, no BYO-MCP, no custom tools, no manifest pinning. |
| **M3** | Evals (hermetic envs, fixtures, graders, certification) | **~30%, but disconnected** | The Worlds stack is a real hermetic eval engine with deterministic evaluators — and the app imports **zero** code from it. In-app: 1 of 4 agents has a suite; certification is version-only. |
| **M4** | Deploy (release bundles, triggers, rollout) | **~10%** | Webhook trigger works but is unauthenticated and non-idempotent. No scheduler, no release bundles, no rollback, no locking, no DLQ, no shadow mode. |
| **M5** | Hardening | **~15%** | Local Docker executor gives real container isolation for Worlds runs only. No adversarial evals, no real Slack, no redaction/retention. |

The honest summary: **M0 is genuinely finished and is the product's strongest asset. M1 is the
bottleneck and is barely started. M3 has more value already built than the score suggests — it is
just sitting in a different directory with no wire to the app.**

---

## 1. What is genuinely built (do not rebuild these)

- **The policy gateway** (`src/server/gateway.ts`) is the real thing: kill switch → tool-grant
  check → connector availability → policy evaluation → allow/deny/require-approval, with a trace
  row written on every branch. The decision ladder is correct and worth preserving verbatim
  through the M1 refactor.
- **Pause/resume across approvals** works, and works statelessly: the deterministic planner
  derives the next action purely from the persisted trace, so a paused run resumes correctly with
  no in-memory state. The live-model path persists `modelMessages` for the same reason.
- **State-based testing.** The 8-scenario Closed-Won suite asserts against actual system state
  (CRM field values, doc existence and content, email presence), never agent self-report. This is
  the hard part of evals and it is done correctly.
- **The promotion gate** blocks on a green suite for the exact candidate version and records a
  credential/policy diff. The mechanism is right; what it certifies is too narrow (§4).
- **The Worlds engine** (`worlds/`): three resettable simulated companies with typed tools, policy
  gates, deterministic evaluators scoring reference trajectories 100/100, a real MCP stdio server,
  and reproducible fixture generation. 18/18 tests pass. This is the hermetic eval backend M3
  asks for, already built.
- **The local Docker executor** (`runtime/local/`): per-run internal network, read-only
  containers, resource limits, exact cleanup. This is M5's isolation story, already built.
- **UI/UX**: light enterprise design system, mobile-responsive, accessible tab/drawer semantics,
  zero horizontal overflow at 390px, no emojis. Not a bottleneck.

---

## 2. M1 — Foundations. The bottleneck.

### 2.1 Approval identity is the most serious gap in the product

`src/app/api/approvals/[id]/route.ts` is eight lines and does this:

```ts
void decideApproval(id, decision, body.approver ?? APPROVER);
```

It never reads the session. The approver name comes from the **request body**, and
`decideApproval` writes it straight into `approval.approver` and the audit event's `actor` field.
It also never checks the approver against `approval.approvers` — the policy's named approver list
(`["revops_lead"]`) is decorative.

Consequences, in order of severity:

1. In the default configuration the endpoint is unauthenticated (`src/proxy.ts` returns
   `next()` when no env vars are set), so **anyone who can reach the app can approve any gated
   action.**
2. The caller **chooses the name that lands in the audit log.** The audit trail — the product's
   central claim — is forgeable by design.
3. Even with `CONVOY_ACCOUNTS_ENABLED=1`, the proxy validates only the cookie's HMAC signature,
   not the session record or its expiry (that logic lives in `currentAccount()`, which only the
   control-plane APIs call). An expired or server-deleted session still passes.

**Fix:** derive the approver from the session server-side, reject when the account is not in the
policy's approver list, and record the account ID (not a display string) on the approval and the
audit event. This is small, and until it exists nothing else in M1 is worth much — every other
governance guarantee rests on "who approved this" being true.

### 2.2 Two more governance holes in the approval path

- **`executeApprovedCall` re-executes without re-checking anything** (`gateway.ts`). No policy
  re-evaluation, no `agent.paused` check, no connector health check. An agent stopped by the kill
  switch between gate creation and approval still gets its gated write executed. Bare `!`
  assertions also throw rather than deny if a connector was detached during the approval window.
- **`timeout_hours` / `on_timeout` are stored on policy rules and never fire.** A gated call waits
  forever. The field's presence in the UI implies an enforcement that does not exist.

### 2.3 Storage, workers, vault

| Item | State | Note |
|---|---|---|
| Demo store → Postgres | **Missing** | `src/server/db.ts` is still a whole-file JSON read/write with a 150ms debounce. `control-plane/postgres-store.ts` is a good working precedent (pooling, `ensureSchema()`, 5 `convoy_`-prefixed tables). |
| Run queue | **Missing** | No `SKIP LOCKED`, no lease, no heartbeat. `advanceRun` is invoked in-process via `void`, so a crash mid-run loses it silently and nothing reconciles it. |
| Workers | **Missing** | The agent loop runs inside the Next.js process. No isolation, and the single-replica constraint follows directly from this plus the in-memory event bus. |
| Run tokens | **Missing** | No JWT/token concept. Not needed until the runtime is extracted, but the gateway signature should be designed for it now. |
| Vault | **Missing** | `credentialRef` is a decorative string (`vault://production/hubspot`). No Secrets Manager, no KMS, nothing to resolve. The "agents never hold credentials" invariant is currently true only because there are no real credentials. |

**Improve, not just build:** `persist()` drops writes while a debounce timer is pending, and
`resetDatabase()` does not clear a pending timer. Both disappear with Postgres — one more reason
to do that migration before adding features on top of the JSON store.

---

## 3. M2 — Tooling. Almost entirely unbuilt.

This is the largest *net-new* build, and it is what makes the product a platform rather than a
demo. Current state:

- **Connectors are 5 hardcoded entries** in `src/server/connectors.ts` with simulated executors in
  `gateway.ts`. Real API calls exist nowhere in the app.
- **No environment CRUD.** Environments are seeded only; there is no `api/environments` route.
  A user cannot create the Sandbox/Production pair the product is built around.
- **No connector attach flow, no OAuth** anywhere in `src/`.
- **No bring-your-own MCP.** Ironically the repo *contains* a working MCP server
  (`worlds/runtime/mcp-server.mjs`) — but the app has no MCP client, so it cannot consume even
  its own.
- **No custom tools** (no HTTP tool definitions, no OpenAPI import).
- **No manifest pinning or drift detection.** Nothing hashes a tool manifest, so "the suite was
  green" cannot survive a connector changing under it.

**Recommended sequencing within M2:** environment CRUD → one real connector end to end
(HubSpot, since the demo already tells its story) → BYO-MCP registration (cheapest breadth, and
the Worlds MCP server is a ready test target) → custom HTTP tools → manifest pinning.
Do manifest pinning *before* declaring M3 complete; certification is meaningless without it.

---

## 4. M3 — Evals. More built than it looks, in the wrong place.

### 4.1 The integration gap

`grep` confirms the Next.js app imports **nothing** from `worlds/`, `runtime/`, or `contracts/`.
So the repo contains two eval systems that do not know about each other:

| | In-app suite | Worlds engine |
|---|---|---|
| Fixtures | inline TypeScript objects, namespaced per test run | versioned JSON world definitions with reproducible generators |
| Reset | delete namespaced rows after the run | first-class `reset` on every world |
| Grading | deterministic assertions vs. system state | deterministic evaluator scoring 0–100 vs. ground truth |
| Coverage | 1 agent template | 3 worlds |
| Wired to the product UI | **yes** | **no** |

`vinny-readme.md` already prescribes the right resolution: *"The Closed-Won Paperwork suite should
become the third World rather than being rewritten. Its fixtures are the seed-state generator,
gateway tools are the typed World surface, and assertions are already the evaluator
specification."* That is the highest-leverage M3 task — it converts a demo fixture into a real
hermetic environment and unifies the two systems instead of maintaining both.

### 4.2 In-app eval gaps

- **Coverage is 1 of 4.** All 8 scenarios target `closed_won_paperwork`. The other three
  templates have zero. Because `promotion.ts` blocks when `suite.total === 0`, **three of the four
  seeded agents can never be promoted through the gate** — yet `seed.ts` pre-creates active
  Production deployments for all four. The seeded state therefore contains production deployments
  that could not have passed the product's own gate. Fix the seed's story or add the suites; right
  now the demo quietly contradicts itself if anyone clicks Promote on Lead Research.
- **No approval scripts.** `TestScenario` has no field describing how to answer a gate, so an
  unattended eval cannot drive a run that pauses. The harness works around this by force-rejecting
  pending approvals at teardown and treating the pause itself as the assertion. That is fine for
  "did it flag?" and useless for "what did it do after approval?" — the entire post-approval half
  of every gated path is currently untestable.
- **Certification is version-only.** The promotion diff carries `{total, passed, version}`.
  Nothing records the model ID, prompt hash, tool-manifest hashes, policy hash, or eval-set
  version. So a model upgrade, a prompt edit, or a policy change silently invalidates a green
  suite while the gate still reports green. The certified tuple from `architecture-v2.md` §3.5 is
  the fix and it is cheap: hash the inputs at test time, store them on the test run, compare at
  promotion.
- **No LLM-judge grader and no human-review queue** for fuzzy outputs (is the invoice email
  correct and professional?). Deterministic-only grading is the right default; the gap matters for
  the "routine but fuzzy" band the product targets.
- **No adversarial/prompt-injection pack.** Agents read CRM notes and emails — untrusted input by
  definition — and nothing measures resistance.

---

## 5. M4 — Deploy. Trigger infrastructure is the weak point.

| Item | State | Consequence |
|---|---|---|
| Webhook trigger | Works, **unauthenticated and non-idempotent** | No signature verification anywhere in `api/webhooks/`; no idempotency keys. A replayed delivery papers a deal twice. |
| Scheduler | **Missing entirely** | `crm_hygiene`'s nightly cron is a stored config string that nothing reads. Its 31 "overnight" runs are fabricated seed history. Any scheduled-agent claim is currently unsupported by code. |
| Release bundles | **Missing** | `Deployment` is `{agentVersionId, agentId, environmentId, status}` — a pointer, not an immutable bundle. No pinned model, no policy snapshot, no manifest hashes, no certification reference. |
| Rollback | **Missing** | No rollback route. Promotion supersedes the previous deployment, so the previous config is recoverable in principle but there is no operation for it. |
| Agent versioning | **Missing** | `api/agents/route.ts` hardcodes `version: 1`. There is no edit → draft → new version flow, so the versioned-asset model the product sells cannot actually be exercised past v1. |
| Per-entity locking | **Missing** | Two runs can paper the same deal concurrently. |
| Retries / DLQ | **Missing** | A failed run is terminal with no retry path and no dead-letter surface. |
| Shadow mode / canary | **Missing** | Design only. |
| Cloud provider adapter | **Partial and bypasses governance** | See `HANDOFF.md` §3 — a `temporal-fargate` run produces zero trace rows, zero policy evaluations, and zero approvals in the app, and the kill switch has no effect on it. |

**The cheapest high-credibility wins here** are webhook signature verification + idempotency keys
(hours, and closes a real double-write bug) and per-entity locking. The scheduler is a genuine
build but small. Release bundles should wait until the certified tuple exists, since the bundle is
what carries it.

---

## 6. M5 — Hardening

- **Container isolation exists for Worlds runs only** (`runtime/local/`), not for the in-app
  runtime, which executes inside the Next.js process. Note the executor *validates then ignores*
  three RunSpec fields — `spec.secrets`, `runtime.network.allowed_hosts`, `artifacts.paths` — so
  the egress allowlist is declared and unenforced. A validated-but-ignored security field is worse
  than an unsupported one; make it honor them or loudly reject them.
- **No real Slack.** `chat.post` writes a row to the demo store. Interactive approvals with signed
  action payloads (the thing that actually drives approval latency down) do not exist.
- **No trace redaction or retention policy**, and traces contain customer data by construction.
- **Audit log is append-only by convention, not tamper-evident.** If "prove it to your auditor" is
  the pitch, hash-chaining is the eventual answer.
- **Trace rows are not actually immutable today**: `crm.read_deal` returns the live object by
  reference, and a later `crm.update_deal` retroactively mutates the earlier read's stored result.
  This directly undercuts the audit story and is a one-line fix (deep-clone on read).

---

## 7. Cross-cutting problems that are not on any milestone

1. **Three execution stacks, one product.** The in-app runtime, the local-Docker Worlds executor,
   and the AWS recursive-agents POC share no code path. Only the first is wired to the UI; only
   the third runs in the cloud; the second has no cloud path at all
   (`infra/cdk/lib/convoy-poc-stack.ts` contains zero references to `world` or `supervisor`).
   Collapsing these behind one provider adapter is the single most valuable architectural task in
   the repo — it is simultaneously the M1 worker extraction, the M3 hermetic wiring, and the M4
   cloud-governance fix.
2. **Two stores with divergent maturity.** The control plane has Postgres, migrations-by-
   `ensureSchema`, and FKs; the product demo has a JSON file. New features keep landing on
   whichever the author touched first.
3. **Demo integrity drift.** The seed asserts things the code cannot do: production deployments
   for agents with no suite, 31 scheduled runs with no scheduler, "no credentials required" copy on
   a landing page whose links all redirect to `/login` when accounts are enabled. Each is small;
   together they are the kind of thing a technical diligence session finds.
4. **`/missions` and `/workspaces` are broken in the default demo config** — both are in the
   sidebar for every visitor, but their APIs call `requireAccount()` / `requireWorkspaceAccess()`
   unconditionally, never consulting `CONVOY_ACCOUNTS_ENABLED`.
5. **No CI.** Everything above is verified by hand. `npm run typecheck`, `npm run build`,
   `npm run world:test`, `npm run world:validate`, and the 8-scenario suite are all scriptable —
   there is no reason a PR can merge without them.

---

## 8. Recommended build order

Sizing is relative (S = hours, M = days, L = weeks) and deliberately coarse.

### Now — credibility fixes (all S, ~one focused day total)
1. Approver identity from the session + approver-list enforcement + account ID on the audit event.
2. `executeApprovedCall` re-checks kill switch, policy, and connector health before executing.
3. Deep-clone tool results so trace rows stop mutating retroactively.
4. Webhook signature verification + idempotency keys.
5. Fix `resumeClaudeRun`'s wrong-tool-result bug (live-model mode, second approval).
6. Make `/missions` and `/workspaces` respect `CONVOY_ACCOUNTS_ENABLED`.
7. Add CI running typecheck + build + `world:test` + `world:validate` + the scenario suite.

Items 1–3 are the ones I would not demo without. They are each small and they each undermine a
claim the product makes out loud.

### Next — M1 foundations (M–L)
8. Demo store → Postgres, reusing `control-plane/postgres-store.ts` patterns. (M)
9. Run queue on the `runs` table + reconciler for orphaned runs. (M)
10. Extract the runtime into a worker; gateway keeps the credential boundary. (L)
11. Real vault behind the gateway; `credentialRef` starts resolving to something. (M)
12. Enforce approval timeouts. (S)

### Then — pick one of two tracks depending on the goal

**If the goal is a design-partner pilot → M2 tooling.** One real connector end to end
(HubSpot), environment CRUD, then BYO-MCP. Without a real connector, nothing leaves the demo.

**If the goal is investor/technical credibility → M3 evals.** Convert the Closed-Won suite into
a World (unifies the two eval systems), add the certified tuple, add approval scripts so
post-approval behavior becomes testable, then extend suites to the other three agents.

My recommendation is **M3 first**: it is mostly wiring work over things that already exist, it
retires the "three execution stacks" problem as a side effect, and the certified tuple is what
makes the promotion gate — the most differentiated thing in the product — actually mean what the
UI says it means.

### Later — M4/M5 as originally scoped
Release bundles carrying the certified tuple, scheduler, rollback, per-entity locking, shadow
mode; then Slack interactive approvals, redaction/retention, adversarial eval pack, and the
runtime's ignored-field cleanup.

---

## 9. What I would cut or defer

- **Per-run container isolation for the in-app runtime** — unnecessary until custom *code* tools
  exist. Gateway-only egress plus a worker process is sufficient while tools are JSON calls.
- **Multi-model routing** — the seam exists (`CONVOY_MODEL`); nothing else is needed yet.
- **The AWS recursive-agents mission surface** as a *product* feature. It is an impressive
  infrastructure POC, but it currently bypasses every governance surface the product sells. Either
  invest in the provider adapter that makes it governed, or keep it out of the demo path. Shipping
  it half-wired is the worst of both.
- **Billing/metering** — design the events during M4 observability; do not build the biller.
