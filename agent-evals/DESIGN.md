# agent-evals — Eval suite + simulation sandbox design

How a routine (mission) is rehearsed and evaluated end-to-end against simulated counterparties and simulated tools, on compressed time, before it ever touches production. Reconciled output of a multi-agent design pass (3 component designs + adversarial coherence verification). Assumes the runtime design in the from-zero plan: append-only per-mission event log in Postgres as sole authoritative state; Mission/Run/PlanVersion/Step/Attempt/Gate/Artifact; two-phase side effects; gates as durable blocks; budget as a reservation ledger.

**The one property everything hangs on: the runtime never knows it is in a sandbox.** Sim and prod differ only in what is injected at the composition root — a clock, tool bindings, a gate resolver. No `if (sim)` branches in agent-runtime. This is what makes "rehearsed before production" and the ablation chart honest, and it is what lets the whole eval stack be built and tested before the runtime exists.

---

## 1. Ownership

| Concern | Lives in | Owner |
|---|---|---|
| ClockPort, library-mode `drain()`, `environment_id` on Mission, `ts`/`wall_ts` event columns | `agent-runtime/` | Aneesh (5-item seam, §2) |
| MCP manifests (single copy), gateway routing/bindings | `environments/` | Aneesh |
| Sim connector emulators + WorldStore, (later) replica importer | `environments/sim/` | **Vinayaka-authored, Aneesh-reviewed** — they sit beside prod connectors to share manifests and contract tests |
| Scenarios, fixtures, counterparty engine, SimClock + DES driver, instance lifecycle, graders, runner, reports, CI wiring, certification registry | `agent-evals/` | Vinayaka |

Rule of thumb: **environments simulates; agent-evals decides.** Counterparty *state* (an inbox) is world; counterparty *behavior* (when the carrier replies, with what) is scenario data executed by the evals driver. Everything is in-process TypeScript + Postgres — no RPC surfaces, no daemons, no queue infra. The suite runner is a CLI (`convoy-evals run <suite>`), on a laptop or in CI.

## 2. The runtime seam — the complete ask to Aneesh (verified minimal)

1. **`ClockPort { now(): Date }` in the shared core package.** All *domain* time flows through it: event `ts`, gate deadlines, durable-timer `fire_at`, retry backoff, the "today is …" string in prompts. Prod injects `() => new Date()`. *Mechanical* time (leases, heartbeats, LLM/HTTP timeouts) stays wall-clock even in sim — leases protect against crashed processes, which are real even when the calendar is fake. Events carry two columns: `ts` (ClockPort) and `wall_ts` (default now(), for latency/cost telemetry). Vinayaka ships the eslint rule banning `Date.now()`/`new Date()` in prompt assembly and timer math; Aneesh just keeps CI green. (~1h in week 1; brutal to retrofit once the log has mixed-provenance timestamps.)
2. **Library-mode drain.** `drain(missionId, {clock}): Promise<DrainReport>` runs the mission until nothing is runnable at `clock.now()` — every live attempt finished, remaining work blocked on a future timer or an open gate. `DrainReport = { terminal, openGates: [{gateId, kind, deadlineAt}], nextTimerAt, stepsExecuted }`. Production wraps this same function in a poller; sim calls it directly. Implied structural rule, stated in CONTRACTS.md: **the runtime stays embeddable** — no daemon assumption, no global singletons, deps injected.
3. **`environment_id` on Mission**, resolved by the gateway to a per-tool binding set (which MCP endpoint serves each tool name). One column + one lookup — and it is the entire routing seam for emulator/replay/live bindings, eval-traffic exclusion in telemetry views, and later shadow mode. (No `triggerType: eval` needed.)
4. **Programmatic `startMission(spec, overrides)`** — no console required; overrides pin model id, prompt versions, policy, budget envelope, environment_id. Budget exhaustion emits a typed event and terminal outcome, never a crash.
5. **`resolveGate(gateId, resolution, {resolvedBy, reason})`** with attribution persisted verbatim (`resolvedBy: 'harness:<scriptStepId>'` vs a human name). Open gates are enumerated by DrainReport — no separate list API.

Restated (already contracted, verify not build): direct-Postgres event reads with per-mission monotonic `seq`; immutable mission-spec snapshot with model/prompt hashes; per-attempt context manifest; tool-manifest + policy hashes exportable. Deferred but flagged: a gateway **recorder** flag persisting tool results keyed by `(tool, canonical-args-hash)` — makes the log double as a replay cassette; lands with first real traffic.

Explicitly **deleted** from earlier drafts (each would quietly grow the runtime): a runtime-owned virtual-clock service / `advanceClock` RPC; `triggerType` stamping; a runtime-enforced promotion door (v1 = `convoy-evals certify-check` CLI before manual promotion); a mission-state RPC (subsumed by drain).

## 3. Virtual time — discrete-event simulation

The harness owns a `SimClock` implementing ClockPort plus `advanceTo()`. Sim time is frozen while work executes (a step is a zero-duration event in sim time); the drive loop advances the clock to the earliest of: the runtime's next durable timer (`DrainReport.nextTimerAt`), the next scheduled counterparty delivery, the next scripted gate resolution. A 45-sim-day renewal runs in minutes of wall-clock (dominated by real LLM latency). A 30s retry backoff persisted as a durable timer is auto-skipped — sim runs never sit in real backoff. Step mode exists for debugging.

```
runUntil(scenario):
  loop:
    report = drain(missionId, { clock: simClock })
    if report.terminal: break
    resolveScriptedGates(report.openGates)          // ApprovalScript, §5; may schedule sim-latency
    deliverDueCounterpartyEvents()                  // §4
    t = min(report.nextTimerAt, nextCounterpartyAt, nextGateResolutionAt)
    if t == null: fail('stalled')                   // quiescent, unblocked, not terminal = runtime bug
    simClock.advanceTo(t)
    enforceWallClockTimeout(scenario.budgets.wallClock)
```

Date perception: the "current date" in every prompt comes from ClockPort. Every date inside fixture documents is authored epoch-relative (`{{t0+45d}}`, `{{t0-2y}}`) and materialized at instantiation; pick `t0` within ~30 days of real today so the model's prior doesn't fight the injected date. A grader-side lint scans context manifests for the *real* wall date leaking into prompts. All domain time is stored UTC, rendered in tenant timezone — one rule, applied everywhere. Event ordering: `(ts, seq)`; zero-duration ties resolve by append order. Harness optimization (named, not deferred): recurring poll timers are coalesced — an empty-inbox poll timer skips directly to the next world-changing event rather than iterating ~6,500 no-op drains across a 45-day mission.

## 4. Simulated world

**Tool surface:** emulators in `environments/sim/` implement the *same MCP manifests* as prod connectors, mounted behind the same governed gateway, selected by `environment_id`. World state (documents, AMS-like records, inboxes, portal states) lives in a per-instance WorldStore seeded from a fixture pack. Shared manifest contract tests run against both emulator and prod connector — including **idempotent-execute cases** (re-executing a side effect with the same idempotency key must dedupe), without which the kill‑9/resume rig proves nothing.

**Counterparties: scripted state machines, deterministic by default.** All *semantics* — whether to reply, after how long, with which attachment, with which wrong field — are script-chosen (seeded PRNG for distributions). No LLM personas in v1 (cut by verification: a flaky counterparty is a confounder in the decay chart, the one artifact that must be above suspicion). Profiles: `cooperative` (replies in 1–2 sim-days, right documents) · `slow` (replies only to the 2nd+ chase — tests that the chase cadence exists) · `wrong-document` (prompt but stale/mismatched attachment) · `silent` · `adversarial-injection` (attachment carries embedded instructions) · `custom` (rules verbatim). Rules: `on` (message-received with matchers / portal-request / sim-time) → `after` (sim delay) → `do` (send-message from template + fixture attachments / portal-transition / nothing), with `maxFires`. Assertions about the *agent's* outbound messages never live in counterparty rules — they are graders. Graders never grade counterparty text.

**Instance lifecycle** (in-process): `createFromFixture(packRef, {t0, seed}) → SandboxInstance` · `instance.world` (live reader for graders/driver) · `instance.exportBundle()` (one content-addressed world bundle at teardown — the single end-state surface graders and kept-on-fail debugging consume) · `destroy()`. One instance per scenario, never reused; teardown always runs, but instances are kept 48h on failure for debugging.

**Fidelity ladder** (each a named graduation, all behind the same manifests):
- **L0 — synthetic fixture packs** (ships first; everything above runs on it). Answer-key-first generation: the key is written, world documents are rendered *from* it, so hand-auditing checks rendering, not truth.
- **L1 — cassettes**: recorded real tool traffic replayed from the gateway recorder. Doubles as connector contract tests (semantic drift detection).
- **L2 — replica**: snapshot import of a design partner's actual data (redacted). Gated on partner data access *and* on prod connector read paths existing — a named dependency on Aneesh's environments work, not assumed. The pitch line "rehearsed in a replica of your environment" is true only from L2; before that, rehearsals are honestly synthetic.
- **L3 — shadow**: candidate version runs against live triggers in a hermetic mirror; diff against the incumbent's real actions. Per-customer at go-live, a gateway policy mode.

Fixture identity: packs are content-addressed as authored (**packHash**, with `{{t0±Nd}}` templates unmaterialized); every run records `(packHash, t0, seed)`. Certification pins packHash + seed; t0 floats within a stated window; graders resolve dates via the same materialization, so verdicts are t0-invariant by construction.

## 5. Scenario anatomy (canonical JSON, zod-validated)

Scenarios are **pure data** — diffable, hashable, capturable from real runs, eventually customer-authorable. `.scenario.ts` is permitted only as authoring convenience that *emits* canonical JSON; the eval-set manifest hashes the JSON. One code escape hatch: a hash-pinned CustomGrader registry.

```
Scenario {
  id, title, missionType, kind: 'single' | 'gauntlet',
  fixture: { packHash, envType, params },        // regeneration knobs recorded; hash authoritative
  trigger,                                        // v1: 'api' | 'schedule' only — the harness starts the
                                                  // mission itself; inbound-message triggers need runtime
                                                  // watcher machinery under drain() and are deferred, named
  counterparties: CounterpartyScript[],
  approvals: ApprovalScript,
  budgets: { usd, tokens?, simTime, wallClock, onExhaustion: 'fail' | 'grade_partial' },
  answerKeyRef: { path, hash },                   // sealed; never reachable from inside the environment
  graders: GraderSpec[],
  items?: ItemSpec[],                             // gauntlet decomposition → Q(n)
  probes?, trials?, provenance, tags
}
```

**ApprovalScript** — every gate the mission may raise gets resolved unattended, *and each scripted resolution doubles as an expected-gate assertion*:

- Modes: `auto_approve {maxGates}` · `auto_reject` · `scripted { steps, onUnexpectedGate: 'fail_scenario' | {resolve} }`.
- `GateScriptStep { expect: GateMatcher (kind, stepTag?, payload matcher), resolve, afterSim? ('4h' — human latency, exercising park-and-resume for free), optional?, ordered?, maxFires? }`.
- Resolutions: `approve` · `reject{reason}` (feeds the retry path — graders then assert recovery) · `provide_input{payload}` · `raise_budget` · `expire` (deliberately let it time out; tests escalation) · `edit_then_approve{patch}` (approve with amended args — feeds the learning label factory).
- At scenario end the harness emits synthetic verdicts: a non-optional step that never matched → `fail (gate_never_raised)`; a gate hit `onUnexpectedGate: fail_scenario` → `fail (unexpected_gate)`. "Paused at the expected gate *before* the side effect executed" is a trajectory grader, so both halves — right gates raised, nothing executed before its gate cleared — are graded from the log.

**Answer keys** are sealed, content-addressed, referenced by `KeyRef` from graders, never inlined in scenarios, never in any prompt or fixture.

**Gauntlets and Q(n):** items carry `ordinal` (n) and a per-item key subtree. Item attribution is by domain key — the mission's own output contract requires per-item artifacts tagged `packet/{policy_number}`, which is just doing the job correctly; an item with no attributable output scores 0 with status `missing`. **Per-item cost:** step charters carry the item domain key, echoed onto step/budget_debit events — that convention is what makes `$-per-verified-item` computable at item granularity (v1 fallback if not yet wired: run-level cost ÷ verified items, labeled as such). Single-item scenarios are the degenerate case — one grading pipeline.

## 6. Graders

Every grader emits the same leaf record — `Verdict { graderId, graderVersion (content hash — stamped from day one; makes later cert-taint a query), scope {runId, itemId?}, status: pass|fail|error|skipped|missing, score 0..1, evidence[] (fail MUST carry ≥1), lowConfidence? }`. `error` is a harness failure, never a subject failure, and is never green. `ItemVerdict` / `ScenarioVerdict` are pure aggregation layers (scenario-level statuses: `unscripted_gate`, `budget_exceeded`, `timeout`, `skipped_budget`, `quarantined`).

1. **End-state** — read from the exported world bundle + artifact store, *never* agent self-report (there is no input type for transcripts): `artifact_exists` / `artifact_field` (JSONPath/regex extract, compare vs KeyRef) / `world_query` / `checklist` (expands the key's checklist into weighted per-item sub-assertions — the gauntlet workhorse).
2. **Trajectory** — over the event log: `never` (denied calls, forbidden tools) · `always` (every send_email's to-domain within the counterparty set) · `event_count` (chase sent ≥2 times; poisoned approach retried ≤1 — the doom-loop check) · `sequence` (intent → gate_raised → resolved → executed → result) · `paused_at_gate` (two-phase invariant sugar) · `eventually` (amended checklist reflected in a plan_version event within 2 sim-days) · `budget_shape`.
3. **Fresh-context LLM judge** — pinned model + content-addressed prompt, versioned with the eval set; rubric-weighted; temperature 0, 3 samples, majority verdict + mean score, 2–1 splits set `lowConfidence`. **The closed input type is the isolation guarantee**: inputs are artifact / key-excerpt / world-query only — there is deliberately no constructor for transcript, messages, plan text, or model_call events, so the worker's reasoning cannot leak into the judge. Calibration is a table, not a doc: every judge verdict on the calibration slice stored against a human label; `judge_labels` rows come free from the label factory (`override-verdict --reason`). Judges ship **advisory-only**; the gating thresholds (≥30 labels, FA ≤5%, FR ≤15% — FA stricter because a false accept in a trust product is the killer) activate when a judge first needs to block promotion.
4. **Behavioral probes** — injected through counterparty channels or world mutation, never in harness voice, graded by ordinary assertions: `criteria_retention` (late in the run, a counterparty asks the agent to restate the *amended* criterion) · `injection_resistance` (payload attachment + mustNot trajectory + mustStill end-state) · `stale_world` (mid-run mutation; final artifact must reflect post-mutation truth).

Every grader carries `class: 'invariant' | 'quality'`. **Invariants (two-phase ordering, no denied calls, injection mustNots, paused-at-gate) must pass in every trial — one violation in one trial is red, regardless of pass rates.** Quality goes through sampling rules. You may be unlucky on quality, never on safety.

## 7. Nondeterminism policy

- **Per-PR CI (Tier 0, $0, <2 min):** replay mode only — graders re-run over recorded event logs + world bundles, bit-stable. Judge calls hit a committed **judge cache** keyed `(rubricHash, evidenceBundleHash, modelId)` storing the *aggregated* 3-sample verdict; a cache miss fails loudly, never silently bills the API. Plus the grader-of-graders suite: scripted `golden` executor (must grade 1.0) and `violator-*` executors (each grader must fail when it should) writing schema-conformant logs via the shared db package — legal because of executor-agnosticism, zero model calls.
- **Nightly smoke (Tier 1, ≤$30):** ~10 scenarios, live, n=1, Convoy arm only. Detects drift, doesn't certify. Fail→retry once; fail-then-pass = candidate-flake with an owner; fail-fail = regression alert with the kept instance.
- **Certification (Tier 2, release tags / promotion, ≤$800):** single scenarios n=5 pass 4/5; gauntlets n=3 trials, per-trial + mean Q(n), aggregate thresholds (slope/AUC/item-floor). **Thresholds live in exactly one place — the versioned eval-set config — fixed ex-ante per the falsification contract, never tuned after seeing a run; changing them is a new evalSetVersion.** Baseline-arm results are cached per (suiteHash, baselineVersion) — the baseline is frozen, so it reruns only when the suite changes.
- **Anti-flake:** retry-until-green is banned. Quarantine is a versioned file with owner + hard 14-day expiry; quarantined scenarios still run but don't gate; an expired entry fails Tier 0 for everyone. Quarantine can never touch a trap scenario or a certification-required check.

## 8. Runner, reports, and the "try it out" surface

**One pipeline, three modes**, differing only in injected driver + gate resolver; grading is a pure function of `(eventLog, worldBundle, scenario)` in all three — the grader cannot tell a fixture from a live run from a rehearsal:

| Mode | Execution | Gates | Clock | For |
|---|---|---|---|---|
| `replay` | recorded logs | in the log | none | PR CI, grader dev — works with zero runtime |
| `live-scripted` | drain() loop | ApprovalScript | SimClock (DES) | smoke, gauntlet, certification |
| `live-rehearsal` | drain() loop | real humans | compressed (counterparty latency compressed; gates wait for humans) | the customer-facing try-it-out |

Pipeline per scenario: `provision → seed → launch → runUntil quiescent → collect (direct Postgres + artifacts; optional --record to fixture) → grade → persist → teardown`. Parallelism is governed by dollars, not infra: `maxConcurrentScenarios` + suite USD cap with cheapest-first admission (a blowout skips expensive scenarios, not many cheap ones); per-scenario budgets enforced by the runtime's own ledger. Evals bookkeeping lives in its own Postgres schema: `suite_runs`, `scenario_runs`, `judge_labels`, `certifications`.

**Suite report** (static self-contained HTML — zero dependency on `website/`): pass/fail matrix by check class · Q(n) decay curve with OLS slope, AUC, first/last-decile delta (CIs deferred until someone disputes a chart) · $-per-verified-item · two comparison views off one component (this tuple vs last = regression view; Convoy arm vs frozen baseline = ablation view) · judge-calibration panel with labeling backlog.

**Rehearsal Report** (the customer-facing sign-off artifact, same pipeline, different template): routine summary + certified tuple in human terms · step-by-step timeline with the exact evidence bundle each approver saw · artifacts produced · **counterparty interaction timeline on a sim-time axis** (the section that makes an ops director *feel* the routine) · verification results with evidence citations · cost projection (min 2 rehearsals before it renders) · residual-risk list (judge-only checks, auto-resolved vs human-resolved gates, quarantine debt) · sign-off block. Signing writes a typed event and the certification row; the PDF hash is stored on the cert.

**Rehearsal sequencing dependency, named:** live-rehearsal routes gates to real humans through the console gate inbox, which ships with Aneesh's skeleton at earliest — so "first rehearsal → first sign-off" is an explicit milestone gated on console gate UX. Fallback until then: founder resolves gates via the concierge CLI while screen-sharing the timeline. V1 is founder-triggered (`convoy-evals rehearse <routine> --gates=human`); no self-serve rehearsal triggering.

## 9. Certification and promotion

One tuple, computed from the log (never hand-entered): `{ routineId, agentVersion, modelId, promptHashes (incl. plan template), gatewayManifestHash (tools + policy folded), packHash/environmentTemplateRef }`. `evalSetVersion` and `harnessVersion` are recorded **on** the cert but excluded from the invalidation hash; promoting any *new* routine always requires the currently pinned set — which achieves suite-evolution hygiene without retroactive revocation contradictions. Any tuple-field change → cert `superseded`, re-certify (this catches the three silent killers: prompt edits, tool-schema drift, policy edits). Judge-taint: if calibration later flunks a judge version, every cert whose green depended on it flips `tainted` — a query, because Verdicts carry graderVersion.

**V1 enforcement is a CLI check** (`convoy-evals certify-check`) run before manual promotion — with five white-glove partners and founders launching every mission, a runtime-enforced promotion door is deferred (the `certifications` table ships now so the door is a later addition, not a migration). Manifest drift: nightly compare of live tuples vs certs → `stale` + auto-scheduled re-cert; production keeps running with runs flagged, grace period before new launches block.

## 10. Build order (one person) and v1 cuts

Order: (1) Scenario/Verdict zod schemas + replay-mode grading over hand-authored fixture logs (first four assertion kinds: `never`, `event_count`, `artifact_field`, `checklist`); (2) scripted golden/violator executors + grader-of-graders CI; (3) SimClock + counterparty driver + ApprovalScript against drain() (walking skeleton or fallback runner); (4) judge + calibration table + judge cache; (5) certification registry + reports; capture CLI (run→scenario — production approval traces become expected-gate assertions for free) only after the first real partner run exists.

**Cut from v1** (verified): LLM counterparty renderer/personas · runtime-enforced promotion door · token-bucket pacing (concurrency cap + USD cap suffice) · bootstrap CIs on slope · random fault injection (deterministic scripted fault plans only) · any RPC between evals and environments. **Deferred with hooks kept:** L1 cassettes (recorder flag) · L2 replica importer (named dependency: partner data + prod connector reads) · L3 shadow · taint machinery + canary slice (activate at first certification; graderVersion stamped from day one) · judge gating thresholds (labels table ships now) · rehearsal PDF/sign-off UI in `website/` (static HTML report first) · customer scenario-authoring form. **Keep despite temptation (load-bearing):** the 5-item runtime seam · golden/violator CI · the three plan traps as probes (criteria-drift, duplicate-docs, doom-loop) + injection profile · invariant-vs-quality two-class rule · stalled-as-verdict · quarantine-with-expiry · frozen naive baseline arm.
