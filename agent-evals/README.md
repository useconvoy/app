# agent-evals

The eval-running suite and simulation sandbox for Convoy. It rehearses a long-running, multi-party, approval-gated routine end-to-end on compressed time — simulated clock, scripted counterparties, unattended gate resolution — and grades the run purely from the event log and world state. The rehearsal report it produces is the customer-facing trust artifact a design partner signs before a routine touches production.

## Quickstart

```bash
npm install
npm test                                  # contract + grader + sandbox tests; no network, no model calls
npm run corpus                            # generate the answer-key-first scenario corpus
npx convoy-evals run \
  --set scenarios/sets/renewal-prep-v1.json \
  --subject scripted:golden               # full suite against the scripted golden executor
```

Other CLI verbs: `replay` (grade a recorded log, bit-stable — what PR CI runs), `report` (static HTML suite/rehearsal report), `lint` (scenario completeness), `dry-run` (admission gate: golden must grade 1.0, a violator must fail). See DESIGN.md §9.

## Directory map

```
src/
  runtime/    Co-signed contracts: event taxonomy (events.ts), runtime seam
              (ports.ts), EventLog (log.ts — JSONL stand-in for the Postgres table)
  schema/     Scenario format (pure JSON + zod), data matchers, Verdict + aggregation
  sandbox/    SimClock, WorldStore + fixture packs, tool gateway + emulators,
              counterparty engine, gate-script engine, DES driver (api.ts = contracts)
  executors/  ScriptedRuntime + golden / violator-* executors, frozen naive baseline
  graders/    End-state, trajectory, fresh-context judge (+cache/calibration), probes
  scoring/    Q(n) decay stats, invariant rule, the suite "green" definition
  runner/     Suite runner (replay | live-scripted | live-rehearsal), budget governor
  reports/    Suite report + rehearsal report (self-contained HTML)
  corpus/     Answer-key-first corpus generation, trap injection
scenarios/    Fixture packs, scenario JSON, versioned eval sets, quarantine.yaml
tests/        CI suite (Tier 0: replay + grader-of-graders, $0, deterministic)
```

## Design

DESIGN.md is the full design: why simulation needs time/people/approvals rather than mock tools, the determinism rules behind the decay chart, the module walkthrough, the fidelity spectrum (L0 synthetic → L3 shadow), CI tiers, and certification.

**Runtime seam, in one line:** everything agent-evals asks of agent-runtime is `ClockPort`, a library-mode `drain()` returning a `DrainReport`, `environment_id` routing, programmatic `startMission`, and `resolveGate` with attribution — defined in `src/runtime/ports.ts`; everything else the harness gets by reading the event log.

## Python implementation (branch `agent-evals-python`)

The harness is also implemented in Python + FastAPI (`convoy_evals/`), sharing the
same language-neutral corpus under `scenarios/` and the same event-log wire format
(camelCase JSON) — Python graders replay TS-recorded runs unchanged, and both
implementations produce identical suite results on the committed corpus.

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                      # 132 tests
.venv/bin/python -m convoy_evals.cli run --set scenarios/sets/renewal-prep-v1.json --subject scripted:golden
.venv/bin/uvicorn convoy_evals.api.app:app   # REST API: /scenarios /sets /suite-runs /replay
```
