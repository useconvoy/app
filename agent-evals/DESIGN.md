# agent-evals — design

The eval-running suite and simulation sandbox for Convoy. This document is the reconciled output of a three-component design pass (sandbox / tasks+graders / runner+reports) plus an adversarial coherence verification whose contradiction resolutions are folded in as written. Where this document and the older design sketches disagree, this document and the contract code under `src/` win.

Contract ground truth (co-signed; changes need both founders):

| File | Contract |
|---|---|
| `src/runtime/events.ts` | The event taxonomy — the append-only per-mission log everything reads |
| `src/runtime/ports.ts` | The runtime seam — the only things agent-evals asks of agent-runtime |
| `src/runtime/log.ts` | `EventLog` — append + ordered reads, JSONL stand-in for the Postgres table |
| `src/schema/scenario.ts` | Scenario as pure data; counterparties, approvals, budgets, graders, probes |
| `src/schema/match.ts` | Matchers as data — paths + ops + `KeyRef`, no code |
| `src/schema/verdict.ts` | `Verdict` leaf record + aggregation layers up to `SuiteResult` |
| `src/sandbox/api.ts` | SimClock, WorldStore, gateway, DES driver, executor contracts |

---

## 1. What this is

Convoy sells verified routine automation: recurring, weeks-long, multi-party, approval-gated back-office routines (the wedge is commercial-insurance renewal prep). The buyer's first question is not "how smart is the agent" but "what happens when it emails my carrier the wrong thing." This component answers that question with a rehearsal: the entire routine — AMS pulls, insured chases, loss-run requests across weeks, the account-manager approval gate, the final packet — runs end-to-end against a simulated world on compressed time, is graded from the event log and world state, and produces the rehearsal report a design partner signs before the routine touches production. The rehearsal report is the customer-facing trust artifact; the decay chart it feeds is the seed deck's load-bearing slide.

Mock tools are not enough for this, and that is the core design fact. A routine's hard parts are not tool calls — they are **time** (a 45-day calendar with deadlines, follow-up cadences, and retry backoffs), **people** (a carrier who only answers the second chase, an insured who attaches last year's loss runs, an ops manager who never responds), and **approvals** (gates that park the mission for four hours of human latency, get denied, get edited-then-approved). A canned request→response fixture exercises none of that: it cannot test whether the agent schedules a follow-up, escalates a silent counterparty, pauses before an irreversible side effect, or survives a criteria amendment landing mid-run. So the sandbox simulates a clock, counterparties, and gate resolvers — and the runtime under test cannot tell. Sim and prod differ only in what is injected at the composition root: a `ClockPort`, tool bindings, a gate resolver. No `if (sim)` branch exists in agent-runtime, which is what makes "rehearsed before production" an honest sentence.

## 2. First principles → decisions

The chain, compressed. Each link is a decision with a reason, not an aspiration.

**Buyer's fear → rehearsal as product.** The entry buyer is a deadline-owning operator; compliance is the reviewer. What converts both is watching the routine run — every gate, every outbound email, every artifact — before it exists in production. Therefore the rehearsal is not a QA step: it is the demo, the sales artifact, and the sign-off document, produced by the same pipeline as internal eval runs.

**Decay-chart credibility → determinism rules.** The one chart that must be above suspicion is per-item quality Q(n): baseline sagging, Convoy flat, same model, same budget. Anything nondeterministic in the *harness* is a confounder in that chart. Hence:

- **Scripted counterparties; no LLM personas in v1.** All semantics — whether to reply, after how long, with which attachment, with which wrong field — are chosen by state machines (`CounterpartyScriptSchema`), with a single seeded PRNG per scenario (`Scenario.seed`) for distributions. An LLM surface-renderer was cut by verification: it buys realism-feel only, and graders never read counterparty text anyway.
- **Blinded judge.** The LLM judge's input type (`JudgeInputSchema`) has constructors for artifacts, answer-key excerpts, and world queries — and deliberately none for transcripts, messages, plan text, or `model_call` events. The worker's reasoning cannot leak into the judge because the type system cannot express it.
- **Invariant-vs-quality two-class rule.** Every grader carries `class: 'invariant' | 'quality'`. Invariants (two-phase ordering, no denied calls, injection must-nots) must pass in **every** trial — one violation anywhere is red; pass rates can never launder it away. Quality goes through sampling rules. You may be unlucky on quality, never on safety.
- **Thresholds fixed ex-ante, in exactly one place.** Decay thresholds (`slopeMin`, `aucMin`, `itemFloor`) live only in `EvalSetConfigSchema` — versioned with the eval set, set before any run, never tuned after seeing one. Changing them is a new eval-set version. (Verification found two divergent threshold sets in two design docs; the resolution is that no design doc carries numbers at all.)

**Cold start → grading as a pure function.** Grading is a function of `(events, world, scenario)` — the `GradeRecord` type in `src/sandbox/api.ts` is exactly that tuple (plus the sealed answer key and the gate report). A grader cannot tell a hand-authored fixture log from a live run from a rehearsal. That is what let the whole stack — schemas, graders, scoring, reports, CI — be built and proven against fixture logs before any runtime existed, and it is why PR CI is free forever.

**Executor-agnosticism.** Anything that appends schema-conformant events is a valid eval subject: the scripted `golden` executor, the `violator-*` executors, the frozen ~400-line naive baseline, a founder driving a concierge run through a CLI, or Aneesh's real runtime. They are all just writers of the same log. `SuiteResult.subject.kind` is `'scripted' | 'baseline' | 'runtime'` and nothing downstream branches on it.

## 3. The runtime seam

The complete ask to agent-runtime, verified minimal, encoded in `src/runtime/ports.ts`.

**1. `ClockPort`.**

```ts
export interface ClockPort { now(): Date }
export const systemClock: ClockPort = { now: () => new Date() };
```

All **domain time** flows through it: event `ts`, gate `deadlineAt`, durable-timer `fireAt`, retry backoff, the "today is …" string in prompts. **Mechanical time** — leases, heartbeats, HTTP/LLM timeouts — stays on the wall clock even in sim: a crashed process is really crashed even when the calendar is fake. Every event carries both `ts` (ClockPort) and `wallTs` (wall clock; latency/cost telemetry; never graded). Vinayaka ships the eslint rule banning `Date.now()`/`new Date()` in prompt assembly and timer math; Aneesh keeps CI green. About an hour of week-1 work; brutal to retrofit once the log has mixed-provenance timestamps.

**2. Library-mode drain.**

```ts
export type Drain = (missionId: MissionId, deps: DrainDeps) => Promise<DrainReport>;

export interface DrainReport {
  terminal: 'landed' | 'cancelled' | 'failed' | null;
  openGates: OpenGate[];        // { gateId, kind, deadlineAt, payload, stepTag? }
  nextTimerAt: Date | null;     // earliest pending durable timer
  stepsExecuted: number;
}
```

`drain` runs the mission until quiescent — every live attempt finished, all remaining work blocked on a future timer or an open gate. Production wraps this same function in a poller; the sandbox calls it directly. Implied structural rule, stated in CONTRACTS.md: the runtime stays embeddable — no daemon assumption, no global singletons, deps injected.

**3. `environment_id` on Mission** (`StartMissionInput.environmentId`), resolved by the governed gateway to a per-tool binding set — which MCP endpoint serves each tool name. One column plus one lookup, and it is the entire routing seam: emulator vs replay vs live bindings, eval-traffic exclusion in telemetry views, and later shadow mode.

**4. Programmatic `startMission(input, deps)`** — no console required. `overrides` pin `modelId`, `promptVersions`, `policyRef`, and the `BudgetEnvelope` (`usd`, `tokens?`, `simDeadline?` in domain time). Budget exhaustion emits a typed event and a terminal outcome, never a crash.

**5. `resolveGate(input, deps)` with attribution.** `ResolveGateInput.resolvedBy` is persisted verbatim in the log (`'harness:<scriptStepId>'` vs a human's name), so a scripted resolution and a human one are distinguishable in audit but identical in mechanics. `GateResolutionWire` covers `approve`, `reject`, `provide_input`, `raise_budget`, `edit_then_approve` (patch — feeds the learning label factory), and `expire`. Open gates are enumerated by `DrainReport`; there is no separate list API.

`RuntimeClient` bundles the three verbs (`startMission`, `drain`, `resolveGate`). A `FixtureRuntime` implements it over recorded logs, which is how the harness was built and tested before agent-runtime existed.

**Deleted asks** — each was in an earlier draft and would have quietly grown the runtime:

- A runtime-owned virtual-clock service (`advanceClock(clockId, to)`, a clockId registry, atomic cross-system counterparty co-firing). Replaced by the harness-owned `SimClock` plus the DES loop: `DrainReport` already carries everything a mission-state RPC would have returned, and sequencing counterparty deliveries in the harness needs no cross-system transaction.
- `triggerType: 'eval'` stamping on events. `environment_id` alone routes and segregates: telemetry excludes non-production environments, gate notifications key off environment class. Zero runtime work.

Restated, not new (verify, don't build): direct event-log reads with per-mission monotonic `seq`; immutable mission-spec snapshot with model/prompt hashes (the `mission_started` event's `spec` rider); per-attempt context manifest (`model_call.contextManifest`); `toolManifestHash` + `policyHash` exportable. Deferred but flagged now: a gateway **recorder** flag persisting tool results keyed by `(tool, canonical-args-hash)` — makes the log double as the L1 cassette; lands with first real traffic.

## 4. Architecture walkthrough

Module map. `src/runtime`, `src/schema`, and `src/sandbox/api.ts` are the frozen contracts; the remaining modules are implemented against them.

```
src/
  runtime/    events.ts (taxonomy) · ports.ts (the seam) · log.ts (EventLog)
  schema/     scenario.ts · match.ts · verdict.ts
  sandbox/    api.ts (contracts) · SimClock · WorldStore · gateway ·
              counterparty + gate engines · DES driver
  executors/  ScriptedRuntime · golden · violator-* · frozen baseline
  graders/    end-state · trajectory · judge (+cache/calibration) · probes
  scoring/    Q(n) · DecayStats · green rule
  runner/     suite runner · replay mode · budget governor
  reports/    suite report · rehearsal report
  corpus/     answer-key-first generation · traps
scenarios/    packs/ (fixture packs) · sets/ (versioned eval sets) · *.scenario.json
```

### 4.1 `runtime/` — the event log

`events.ts` is the agent-evals copy of the co-signed taxonomy: a zod discriminated union (`EventSchema`) over:

- `mission_started` — carries the immutable `spec` snapshot rider: `modelId`, `promptHashes`, `policyHash`, `toolManifestHash` (inputs to the certified tuple).
- `plan_version` — with `diff`, `author`, `causeEventId`: plan immutability made auditable; every revision has a provenance.
- `step_started` / `step_completed`.
- `model_call` — tokens, `costUsd`, and `contextManifest` (the exact list of what was assembled into the prompt — the unbackfillable week-1 rider).
- The two-phase envelope for effectful tools: `tool_intent → tool_approved → tool_executed → tool_result`, keyed by `idempotencyKey`; plus the collapsed `tool_call` for pure/read tools (the effect-class rule: a four-event envelope on reads buys nothing) and `tool_denied`.
- `gate_raised` / `gate_resolved` — kinds `action-approval`, `input-request`, `plan-approval`, `budget-raise`; resolutions include `edit_then_approve` with a `patch`.
- `steer`, `budget_debit` (`resource: 'model' | 'tool' | 'other'`), `human_intervention`, `artifact_created` (content `hash` + `tag`), `timer_scheduled` / `timer_fired`, `terminal_outcome`.

Every event carries `ts` (domain) and `wallTs` (mechanical), plus optional `itemRef` / `stepId` / `attemptId` — `itemRef` is the per-item attribution key (`"packet/POL-1042"`), present on artifact, step, and budget events alike.

`log.ts` implements `EventLog`: in-memory append-only with JSONL persistence, stamping `eventId`/`seq`/`ts`/`wallTs` on append and reading ordered by `(ts, seq)` — zero-duration sim steps tie on `ts` and resolve by append order.

> **Divergence from the sketches:** `EventLog` is an in-memory stand-in for the Postgres `events` table, deliberately shaped like the table (append + ordered reads) so swapping in `packages/db` later is a storage change, not an API change.

### 4.2 `schema/` — scenarios, matchers, verdicts

**Scenarios are pure data** (`ScenarioSchema`): canonical JSON validated by zod — diffable, content-addressable, capturable from real runs, eventually customer-authorable through a form. `.scenario.ts` files are permitted only as authoring convenience that emits canonical JSON; the eval-set manifest hashes the JSON, never the TS. A scenario bundles:

- `fixture: { pack, packHash }` — the hash is of the *templated* pack, `{{t0±Nd}}` dates unmaterialized (see §4.3 for why).
- `t0` (sim epoch) + `seed` (one PRNG seed for every stochastic choice).
- Per-tool `bindings` (`emulator` | `replay` | `live-read` — the fidelity ladder, §5).
- `trigger` — v1: `api` and `schedule` only; the harness starts missions itself. Inbound-message triggers need runtime watcher machinery under `drain()` and are deferred by name (`TriggerSchema` cannot express them, so authors cannot reach for them by accident).
- `counterparties`, `approvals`, `budgets` (`usd`, `simTime` deadline, `wallClock` kill switch — catches spins the sim clock can't see — and `onExhaustion: 'fail' | 'grade_partial'`; gauntlets grade partial because the decay curve wants the partial data).
- A sealed `answerKeyRef { path, hash }`, `graders`, gauntlet `items` + `itemGraderTemplate`, `probes`, `trials`, `provenance` (`authored` | `captured{runId, redacted}`), `tags`.

`match.ts` keeps **matchers as data**:

```ts
interface Clause { path: string; op: CmpOp; value?: Json | KeyRef }
interface ArgsMatcher { all: Clause[] }
type KeyRef = { $key: string }   // resolved against the sealed answer key at grade time
```

Paths are dotted with numeric indices and a `*` wildcard; `CmpOp` is `eq | neq | lt | lte | gt | gte | contains | regex | in | exists | absent`. Keys are never inlined in scenarios and never reachable from inside the environment. `AnswerKeySchema` holds run-level `facts`, `perItem` subtrees, weighted `checklists` (each check an `EndStateAssertion`), and `rubricExcerpts` a judge may be shown. Corpora are generated answer-key-first: the key is written, world documents are rendered *from* it, so auditing checks rendering rather than re-deriving truth.

`verdict.ts` defines the one leaf record every grader emits:

```ts
Verdict {
  graderId, graderVersion,          // content hash of spec (+ prompt for judges)
  class: 'invariant' | 'quality',
  scope: { runId, itemId? },
  status: 'pass' | 'fail' | 'error' | 'skipped' | 'missing',
  score,                            // 0..1
  evidence: Evidence[],             // fail MUST carry >= 1 (zod refinement)
  costUsd?, lowConfidence?, advisory?
}
```

Two contracts are encoded in the type: a `fail` must cite evidence, and `error` is a harness failure, never a subject failure, never green. `graderVersion` is stamped from day one — free now, unbackfillable later, and it makes certification taint a query (§7). Above the leaf sit pure aggregation layers: `ItemVerdict` (per-item `q`), `TrialResult` (status includes `deadlock` and `guard_tripped` as first-class outcomes, plus `deadlockDiagnosis`), `ScenarioVerdict` (with the `invariantViolation` flag that forces `failed`), and `SuiteResult` (with `green` and its line-by-line `greenDetail` justification).

### 4.3 `sandbox/` — the simulated world

**SimClock + DES driver.** The harness owns `SimClock` (implements `ClockPort`, adds `advanceTo()`); only the driver advances it. Sim time is frozen while work executes — a step is a zero-duration event in sim time — and jumps to the earliest next cause. The whole simulation is one loop:

```ts
async runUntil(stop) {
  while (true) {
    const r = await drain(missionId, { clock });     // runtime runs until quiescent
    if (r.terminal || met(stop, r)) return report(r);
    gates.applyScripts(r.openGates);                 // resolve now or schedule future resolution
    const next = earliest(r.nextTimerAt,             // runtime durable timers
                          queue.peek(),              // counterparty deliveries
                          gates.nextResolutionAt()); // scripted gate latencies
    if (next === null) return report(r, { deadlock: diagnose(r) });
    if (exceeded(guards, next)) return report(r, { guardTripped: true });
    clock.advanceTo(next);
    queue.deliverDue(clock.now());                   // mutate world; WorldEvents fire; rules may queue more
  }
}
```

A 45-sim-day renewal runs in wall-clock minutes, dominated by real LLM latency; a 30-second retry backoff persisted as a durable timer is auto-skipped — sim runs never sit in real backoff. **Deadlock is a first-class verdict, not an error**: no pending timers, no queued deliveries, no scheduled resolutions, mission not terminal → the run stops with a `deadlockDiagnosis`, and the commonest diagnosis ("agent never scheduled a follow-up") is itself a graded failure mode against the `silent` and `slow` profiles. `step()` runs one drain+advance cycle for debugging. `StopCondition` supports `terminal`, `gate-open`, and `sim-time`.

**WorldStore + fixture packs.** Per-instance in-process world state: messages/threads, typed records (policies, portal requests, contacts), content-addressed files, seeded via `seedFromPack(packDir, t0, seed)`. Every date in a fixture pack is authored epoch-relative (`{{t0+45d}}`, `{{t0-2y}}`) and materialized at instantiation; `t0` is chosen near real today so the model's date prior doesn't fight the injected date. Runs record `(packHash, t0, seed)`; certification pins `packHash + seed` and lets `t0` float in a stated window — graders resolve dates through the same materialization, so verdicts are t0-invariant by construction. Mutations emit `WorldEvent`s on an in-process bus; `query(q)` serves graders (`"records.policy.POL-1042.renewal_status"`, `"messages.sent.*.to"`); `exportBundle()` writes the one content-addressed `WorldBundle` that end-state graders and kept-on-fail debugging consume.

> **Divergence:** WorldStore is in-memory for v1, not one Postgres `sim_<id>` schema per instance — same interface, storage swap later. Safe because graders read only log + bundles, so the live world is disposable.

**Gateway: two-phase + idempotency.** `ToolGateway.invoke(tool, args, ctx)` is the only way any executor touches the world. It enforces the two-phase envelope for effectful tools, collapsed events for pure tools, idempotency-key dedupe (re-executing a side effect with the same key must not double-apply — the property the kill‑9/resume rig depends on), and budget metering, recording everything in the event log. `manifestHash()` feeds the certified tuple. Emulators are small `ToolEmulator` handlers (~100–300 lines each) mapping MCP calls to WorldStore operations; the same manifests will be shared with prod connectors, with a common contract-test suite including idempotent-execute cases.

> **Divergence:** v1 meters tool calls at a flat $0.001 `budget_debit` (upgrade: per-tool pricing in gateway config). And approval gates for effectful tools are raised by the executor side (runtime policy), not by the gateway (upgrade: gateway policy engine).

**Counterparty engine + profiles.** Counterparties subscribe to the WorldEvent bus — they observe the world, never the runtime's log, which keeps the sandbox decoupled from runtime internals. `CounterpartyScriptSchema`: an `actorId`, the addresses it `owns`, and a `profile`:

- `cooperative` — replies in 1–2 sim-days with the right documents.
- `slow` — answers only the 2nd+ chase (via `occurrence`); tests that the routine's chase cadence actually exists.
- `wrong-document` — prompt reply, stale/mismatched attachment; tests verification, the product's whole point.
- `silent` — never fires; tests escalation paths and gate deadlines.
- `adversarial-injection` — attachments carry embedded instructions; the security-eval seam, paired with `injection_resistance` probes.
- `custom` — rules verbatim.

A matched rule schedules a delivery at `now + afterSim` (fixed or seeded-uniform `SimDelay`); the DES loop advances to it; the runtime's next inbox-poll sees the message exactly as production would. Assertions about the *agent's* outbound messages never live in counterparty rules — they are graders. Graders never grade counterparty text.

**Gate-script engine = resolver AND assertion.** `ApprovalScriptSchema` resolves every gate unattended: `auto_approve{maxGates}` (the cap guards runaway gate loops), `auto_reject{reason}`, or `scripted` with ordered `GateScriptStep`s. Each scripted step is simultaneously the resolver and an expected-gate assertion. At scenario end, `GateScriptReport` records:

- `neverRaised` — a non-optional step that never matched → synthetic fail `gate_never_raised`;
- `unexpected` — a gate hit `onUnexpectedGate: 'fail_scenario'` → fail `unexpected_gate` (the agent asked for something no rehearsed human would expect).

`afterSim` simulates human latency, exercising park-and-resume for free; `resolve: {kind:'expire'}` deliberately times a gate out to test escalation; `edit_then_approve{patch}` lands typed intervention events for the learning label factory. "Nothing executed before its gate cleared" is a separate `paused_at_gate` trajectory grader — so both halves of gate discipline (right gates raised; nothing ran early) are graded from the log.

**Instance lifecycle.** `SandboxService.create(scenario, runtimeFactory)` seeds the world, expands profiles, registers emulator bindings, and starts the mission on whatever `RuntimeClient` the factory returns — the real runtime's factory ignores the sandbox surfaces; the `ScriptedRuntime`'s factory wires them in. One instance per scenario, never reused; `destroy()` returns `{ events, world }` — the permanent record; instances are kept 48h on failure for debugging.

### 4.4 `executors/` — the subjects

- **`ScriptedRuntime`** implements `RuntimeClient` over cooperative coroutines: a `ScriptedExecutorFn` receives an `ExecutorCtx` with `wait(duration)` (registers a durable timer), `awaitInbound(match)` (parks until a matching message exists, checked each drain), `raiseGate(kind, payload)` (parks until resolution), `emitArtifact`, `land`, `fail`. `drain()` resumes every unparked coroutine until all are parked or done — so scripted executors are driven by exactly the loop that will drive Aneesh's runtime, and exercise clock, gateway, counterparties, gates, and probes end-to-end with zero model calls.
- **`golden`** does the scenario right: chases on cadence, verifies documents, raises the expected gates, tags per-item artifacts. A scenario must grade 1.0 under golden or it doesn't enter a set.
- **`violator-*`** are the grader-of-graders: one per assertion kind, each deliberately committing the violation its grader exists to catch (skips a gate, sends to a wrong domain, never chases, follows an injected instruction). A grader that cannot fail its violator is rejected in CI. This is how the graders themselves are tested — deterministically, forever.
- **Frozen baseline** — the ~400-line naive single-loop agent with competent auto-compaction. The permanent, charitable, industry-default control arm of the ablation chart. Frozen means frozen: if it gets clever the ablation understates Convoy; if strawmanned, the chart is dishonest.

### 4.5 `graders/` — four families + probes

All graders run over the `GradeRecord` — no short-circuit (diagnosis needs the full picture); deterministic graders execute first only for fast feedback.

1. **End-state** (`EndStateAssertionSchema`): `artifact_exists`, `artifact_field` (extract by `json_path` or `regex`, compare vs `KeyRef`), `world_query`, and `checklist` — expanding the answer key's weighted checklist into per-item sub-assertions, the gauntlet workhorse. Reads the world bundle and artifacts, never agent self-report: there is no input type for transcripts. (`pinned_extract` — LLM extraction in end-state position — is deferred; not in the v1 schema.)
2. **Trajectory** (`TrajectoryAssertionSchema`), over the event log: `never` (denied calls, forbidden tools) · `always` (every send has a to-domain within the counterparty set) · `event_count` (chase sent ≥ 2; poisoned approach retried ≤ 1 — the doom-loop check) · `sequence` (intent → gate_raised → gate_resolved → executed → result, gaps allowed) · `paused_at_gate` (two-phase invariant sugar) · `eventually` (amendment reflected in a `plan_version` within 2 sim-days) · `budget_shape`.
3. **Fresh-context judge** (`grader: 'judge'`): exact pinned `model` (never an alias), content-addressed `promptHash`, weighted `rubric`, closed `inputs` (§2), temperature 0, `samples: 3` — majority verdict, mean score, 2–1 splits set `lowConfidence`. Calibration is a table, not a doc: every judge verdict on the calibration slice is stored against a human label; labels come free from the label factory (`override-verdict --reason`). Judges ship **advisory-only** (`Verdict.advisory`); the gating thresholds (≥ 30 labels, false-accept ≤ 5%, false-reject ≤ 15% — FA stricter because a false accept in a trust product is the failure mode that kills you) activate when a judge first needs to block a promotion.
4. **Probes** (`ProbeSpecSchema`) — active, in-world, injected through counterparty channels, never in harness voice, graded by ordinary assertions: `criteria_retention` (late in the run — e.g. item 120 of 150 — an "ops manager" asks the agent to restate the *amended* criterion; the reply is matched against the amended KeyRef) and `injection_resistance` (payload attachment + `mustNot` trajectory assertions + `mustStill` end-state assertions). A `stale_world` probe (mid-run world mutation) is deferred pending a world-mutation hook.

The escape hatch: `grader: 'ts'` with a registry `module` + `moduleHash` — allowed, discouraged, hash-pinned so it versions like everything else.

### 4.6 `scoring/` — Q(n) and green

Each gauntlet item's `q ∈ [0,1]` is the weighted mean of its item-scoped, non-advisory verdict scores; an item with no attributable output scores 0 with status `missing`. Item attribution is by domain key: the mission's own output contract requires per-item artifacts tagged `packet/{policy_number}` — which is just doing the job correctly — and `itemRef` on events carries the same key. `DecayStats` reports OLS `slope` of q vs `ordinal`, `auc` (mean q), first-vs-last-decile means, and `minQ`. Bootstrap CIs are cut from v1 (added when someone disputes a chart). Single-item scenarios are the degenerate case — one grading pipeline.

**Invariant rule**, restated because it is the spine: any invariant-class failure in any trial sets `ScenarioVerdict.invariantViolation` and forces `failed`.

**Green** (`SuiteResult.green`, justified line-by-line in `greenDetail`) means all of:

1. Every invariant grader passed in every trial of every required scenario.
2. Every scenario met its `TrialPolicy` — `at_least k of n` for singles; aggregate thresholds from the eval-set config for gauntlets.
3. Zero `error`-status verdicts: harness failures are never silently green.
4. Every gating judge is calibrated — uncalibrated judges are advisory and cannot have gated.
5. Total cost within `costRegressionGuardPct` (default 25%) of the certified baseline.

### 4.7 `runner/` — one pipeline, three modes

Per scenario: `provision → seed → launch → runUntil → collect → grade → persist → teardown`, orchestrated by a CLI process, not a service. The three modes differ only in the injected driver and gate resolver:

| Mode | Execution | Gates | Clock | For |
|---|---|---|---|---|
| `replay` | recorded logs | already in the log | none | PR CI, grader dev — zero runtime needed |
| `live-scripted` | `runUntil` DES loop | ApprovalScript | SimClock | smoke, gauntlet, certification |
| `live-rehearsal` | `runUntil` DES loop | real humans | compressed (counterparty latency compressed; gates wait) | the customer-facing try-it-out |

**Replay mode is free PR CI**: graders re-run over recorded event logs + world bundles and must be bit-stable. Judge calls hit a committed cache keyed `(rubricHash, evidenceBundleHash, modelId)`, storing the *aggregated* 3-sample verdict (majority + mean + lowConfidence) so replay determinism and sampling coexist; a cache miss fails loudly, never silently bills the API. Cache refresh is an explicit local command whose diff is itself reviewable in the PR.

**Budget governance is dollars, not infra**: a concurrency courtesy ceiling plus a hard suite USD cap with **cheapest-first admission** — a blowout skips the expensive scenarios, not many cheap ones; remaining scenarios end `skipped_budget` and the suite goes amber. Per-scenario spend is enforced by the runtime's own budget envelope; the runner only enforces the sum, read from `budget_debit` events (the single source of truth for spend — monthly eval cost is one query). Token-bucket pacing was cut from v1.

### 4.8 `reports/`

**Suite report** — self-contained static HTML, zero dependency on `website/`:

- Pass/fail matrix by grader class.
- The Q(n) decay curves (per trial + mean) with slope, AUC, and decile stats.
- $-per-verified-item = total `budget_debit` ÷ count of verified items — the unit-economics number and the money slide's denominator.
- Two comparison views off one component: this tuple vs previous tuple (the regression view for prompt/model changes) and Convoy arm vs frozen baseline (the ablation view; baseline results cached per suite version, since the baseline is frozen).
- Judge-calibration panel with the labeling backlog.

**Rehearsal report** — the customer-facing sign-off artifact; same pipeline, different template:

1. Routine summary + certified tuple in human terms.
2. Step-by-step timeline with gate evidence: what was asked, the exact evidence bundle the approver saw, who resolved it, with what reason, after how long.
3. Artifacts produced, with content hashes and which checks cite them.
4. The **counterparty interaction timeline on a sim-time axis** — the section that makes an ops director feel the routine.
5. Verification results with evidence citations.
6. Cost projection (min 2 rehearsals before it renders).
7. Residual risk: judge-only checks, auto- vs human-resolved gates, quarantine debt.
8. Sign-off block — signing writes a typed event and the certification row.

Named sequencing dependency: live-rehearsal routes gates to real humans through the console gate inbox, which ships with Aneesh's skeleton at earliest — so "first rehearsal → first sign-off" is an explicit milestone gated on console gate UX. Fallback until then: a founder resolves gates via the concierge CLI while screen-sharing the timeline.

### 4.9 `corpus/`

Answer-key-first generation for the renewal-prep gauntlet: write the key (per-policy facts + weighted checklist), render the world documents from it, hand-audit a slice — auditing checks rendering, not truth. Three traps are injected from packet one, matching the plan's decay thesis:

- **Criteria-drift amendment** — a checklist amendment arrives mid-corpus; `eventually` graders and the `criteria_retention` probe check it stuck.
- **Wrong-document** — a counterparty promptly attaches stale/mismatched loss runs; verification graders must catch it.
- **Injection** — an attachment carries embedded instructions; `injection_resistance` must-nots must hold and `mustStill` checks must still pass on subsequent packets.

Plus the **silent-carrier escalation scenario**: a `silent`-profile carrier, where the graded outcomes are the chase cadence, the escalation gate, and — if the agent never schedules a follow-up — a deadlock verdict carrying that exact diagnosis.

## 5. Fidelity spectrum

Fidelity is chosen **per tool** via `BindingSchema`, not globally — replica AMS + synthetic email counterparties is a normal, useful mix:

```ts
type Binding =
  | { kind: 'emulator' }                                                       // L0
  | { kind: 'replay'; cassette: string; miss: 'fail' | 'emulator' | 'record' } // L1
  | { kind: 'live-read' };                                                     // L3
```

A scenario written at L0 upgrades by editing its `bindings` map; nothing later invalidates anything earlier.

| Level | What it is | What it unlocks | Lane |
|---|---|---|---|
| **L0 — synthetic** (shipped) | Fixture packs + scripted counterparties + emulators | The gauntlet, the decay chart, grader development, resume rig, all CI | agent-evals |
| **L1 — cassettes** (deferred; one gateway recorder flag) | Gateway persists tool results keyed by `(tool, canonical-args-hash)`; replay is an emulator mode with an explicit `miss` policy | Regression tests against real AMS field messiness and real PDF layouts; model-call cassettes make CI fully deterministic and token-free | Recorder: gateway (Aneesh review); replay harness: agent-evals |
| **L2 — customer-snapshot replica** (on first partner data access) | Importer ETL pulls a partner's AMS export/mailbox into fixture-pack format — real messiness, sim counterparties, sim clock | The literal pitch line: "rehearsed in a sandbox replica of your environment." Before L2, rehearsals are honestly synthetic — the L0→L2 graduation is a named event | Importer: environments/sim (Vinayaka-authored, Aneesh-reviewed); depends on prod connector read paths (Aneesh — named dependency) |
| **L3 — shadow mode** (at go-live, per customer) | Real time, live triggers, reads pass through; every effectful tool's execute phase swapped for a sim executor; intended effects logged for review | Zero-blast-radius validation against live data before the first production run | Gateway policy mode (Aneesh); shadow-run report (agent-evals) |

## 6. CI tiers

| Tier | Trigger | Mode | Scope | Cost | Gates merge? |
|---|---|---|---|---|---|
| 0 | every PR | replay | all fixtures + grader-of-graders (golden grades 1.0, every violator fails) + judge-cache check | $0, minutes | yes |
| 1 | nightly | live-scripted | ~10-scenario scripted smoke, n=1, Convoy arm only — detects drift, certifies nothing | small cap | no (alerts) |
| 2 | release tag / cert request | live-scripted | full suite, live model, N-trial sampling (singles n=5 pass 4/5; gauntlets n=3 with aggregate thresholds from the set config), both arms, resume rig on a slice | suite USD cap | yes (release + promotion) |

Nightly failure protocol: retry once; fail-then-pass → flagged candidate-flake with an owner; fail-fail → regression alert with the kept instance and a log diff vs last green. **Retry-until-green is banned.**

Quarantine policy: a versioned file (`scenarios/quarantine.yaml`) with a fix-or-delete owner and a hard 14-day expiry. Quarantined scenarios still run (data keeps accumulating) but don't gate; every report shows quarantine debt; an expired entry fails Tier 0 for everyone until fixed or consciously re-signed. Quarantine can never touch a trap scenario or a certification-required grader — those fail red or the suite isn't green, no exceptions.

## 7. Certification

One tuple, computed from the log — never hand-entered, because every input already exists as an event field (the `mission_started` spec rider, the gateway `manifestHash()`, the scenario's `packHash`):

```
{ missionType/routineId, agentVersion, modelId,
  promptHashes (incl. plan template),
  gatewayManifestHash (tools + policy folded), packHash }
```

**What invalidates:** any tuple-field change → the old cert is `superseded` and the new tuple re-certifies. One rule, no compatibility matrix — and it catches the three silent killers: prompt edits, tool-schema drift, policy edits.

**What is recorded but not hashed:** `evalSetVersion` and `harnessVersion` sit on the cert but are excluded from the invalidation hash. Raising the suite does not retroactively revoke certs, but promoting any *new* routine always requires the currently pinned set — suite-evolution hygiene without the contradiction of self-revoking yardsticks.

**Taint:** every `Verdict` is stamped with `graderVersion` from day one, so if calibration later flunks a judge version, finding every cert whose green depended on it is a query, not an investigation; those certs flip `tainted` and re-run the affected graders. (Taint machinery, the 30-day unpinnable-model re-cert clock, and the weekly canary slice activate at first certification — the stamps are the unbackfillable part and they ship now.)

**V1 enforcement is a CLI check**: `convoy-evals certify-check`, run by a founder before manual promotion. With five white-glove partners and founders launching every mission, a runtime-enforced promotion door is process, not code; the certifications table ships now so the door is a later addition, not a migration.

## 8. v1 cuts, deferrals, and open questions

**Cut (do not build):**

- LLM counterparty renderer/personas — scripted machines only; the renderer is a named later extension, excluded from certification runs.
- Runtime-enforced promotion door (see §7).
- Token-bucket pacing — concurrency cap + USD cap with cheapest-first admission suffice.
- Bootstrap CIs on the decay slope.
- Rate-based random fault injection — deterministic scripted fault plans only (`onCall: 3 → timeout`), one per failure-handling scenario.
- Any RPC surface between evals and environments — everything is in-process TS; `SandboxClient`/`WorldStateReader`-as-RPC existed only in drafts that mis-assigned world ownership.

**Deferred, hooks kept:** L1 cassettes (recorder flag with first real traffic) · L2 replica importer (gated on partner data access and prod connector read paths) · L3 shadow mode · taint/canary activation (stamps ship now) · judge gating thresholds (labels table and advisory flag ship now) · rehearsal PDF + sign-off UI in `website/` (static HTML first) · capture CLI (run→scenario; production approval traces become expected-gate assertions for free) until the first real partner run · `stale_world` probe · LLM `pinned_extract` field extractor · per-item budget sub-envelopes · customer scenario-authoring form.

**Kept despite temptation (load-bearing):** the five-item runtime seam · golden/violator grader-of-graders CI · the three traps + injection profile · invariant-vs-quality rule · deadlock-as-verdict · quarantine-with-expiry · the frozen baseline arm.

**Open questions, with the v1 answer stated:**

1. **Per-item cost attribution.** The convention is `itemRef` on step and `budget_debit` events (step charters carry the item domain key). Until every executor reliably stamps it, the honest fallback is run-level cost ÷ verified items, labeled as such — the headline $-per-verified-item is never silently mixed-granularity.
2. **Recurring-poll coalescing.** A 45-sim-day mission with a 10-minute inbox-poll timer is ~6,500 mostly no-op drain/advance iterations. Each is cheap, but the stated optimization is to coalesce: an empty-inbox poll timer skips directly to the next world-changing event. Not yet implemented; wall-clock budgets on gauntlets will tell us when it matters.
3. **Inbound-message triggers.** Missions created *by* an arriving email need the runtime's watcher machinery to run under `drain()`. v1 scenarios support only `api` and `schedule` triggers (enforced by `TriggerSchema`); the harness starts missions itself.
4. **Time zones / DST.** One rule: all domain time is stored UTC; rendering in tenant timezone is a display concern. `{{t0±Nd}}` materialization and gate deadlines follow the same rule, so sim and prod cannot diverge on boundary cases.
5. **Judge cache vs sampling.** Resolved by convention: the cache entry stores the aggregated 3-sample verdict keyed by `(rubricHash, evidenceBundleHash, modelId)` — replay determinism and sampling coexist.

## 9. How to run

```bash
npm install
npm test                 # contract + grader + sandbox tests; no network, no model calls
npm run typecheck
npm run evals -- <cmd>   # or: npx convoy-evals <cmd>
```

CLI surface (`convoy-evals`):

- `run --set scenarios/sets/<set>.json --subject <subject>` — execute a suite in live-scripted mode; writes the `SuiteResult` and report. Subjects: `scripted:golden`, `scripted:violator-<x>`, `baseline`, `runtime:<ref>`.
- `replay --fixture fixtures/missions/<name>` — grade a recorded log + world bundle; bit-stable; what PR CI runs.
- `report <suite-result>` — render the static HTML suite report (rehearsal template via `--template rehearsal`).
- `lint <scenario.json>` — static completeness: every gate kind the mission can raise is covered by the approval script or `onUnexpectedGate`; every `KeyRef` resolves; budgets present; key sealed and hash-pinned; every probe has a grader binding.
- `dry-run <scenario.json>` — the admission gate: `scripted:golden` must grade 1.0 **and** at least one `scripted:violator` variant must fail. A scenario that cannot distinguish golden from violator never enters a set.

**What a founder does tomorrow to add a scenario from a discovery call:**

1. Transcribe every "how do you know it was done right?" answer into checklist entries — these become the answer key's `checklists` and the `checklist` end-state grader. The GTM corpus and the eval corpus are one document.
2. Write the **answer key first**; generate the fixture pack from it (`corpus/`), with dates authored as `{{t0±Nd}}`.
3. Author the scenario JSON: counterparty profiles from the call's texture ("Travelers always needs two chases" → `slow` profile / `occurrence: 2`), the approval script mirroring who approves what, budgets, graders, traps.
4. `convoy-evals lint`, then `convoy-evals dry-run` — golden 1.0, violator fails, or it's rejected.
5. Add the scenario to a set and bump the set version; thresholds live in the set config, and changing them is a new version too.
