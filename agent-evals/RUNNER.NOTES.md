# RUNNER.NOTES.md — suite runner / reports / CLI

Owner: runner+reports+CLI agent. Files owned: `src/runner/*.ts`, `src/reports/*.ts`,
`src/cli/index.ts`, `tests/runner.test.ts`. No contract files were modified.

## Sibling readiness at hand-off

ALL sibling components landed during this build: `src/sandbox/index.ts`
(`createSandboxService(opts?: { packRoot? })` — exact contract match),
`src/executors/*` (exact match), `src/graders/index.ts` (`gradeTrial(record,
opts?: { judgeMode? … })` — exact match), `src/scoring/index.ts`, and the
corpus. `npx tsc --noEmit` is fully clean project-wide.

Scoring landed with **positional** signatures rather than the opts-bags this
runner initially assumed; the runner was adapted to the real shapes:
`buildTrialResult(trialIdx, runId, report, verdicts, scenario, costUsd,
simDays, wallMs)` (status derived inside from the report),
`buildScenarioVerdict(scenario, trials, config, opts?: { quarantined? })`
(native quarantine support — the runner's post-hoc override was removed), and
`buildSuiteResult(input)` (matched the assumption). No scoring changes needed.

**Import-shape mismatch found**: `src/graders/world-query.ts` landed exporting
`makeBundleQuery` where the contract names it `bundleQuery`. The runner goes
through `src/runner/world-query-shim.ts`, which accepts either name at runtime,
so this works today and keeps working if the graders side renames to the
contract name. Graders agent: please export `bundleQuery` (alias is fine).

**Cross-sibling mismatch observed (corpus vs sandbox — not runner-owned)**:
`scenarios/packs/renewal-12/pack.json` file entries are `{path, name, mime}`
with content on disk at `<packDir>/<path>`, but `src/sandbox/world.ts`'s
`PackManifestSchema` requires a `contentFile` field per entry and reads
`join(packDir, pf.contentFile)`. Verified by driving
`createSandboxService().create(renewal-golden-3, scripted golden)` directly:
`seedFromPack` throws zod invalid_type on every `files[i].contentFile`. Until
one side moves (sandbox falling back to `path` seems cheapest), every live run
— including tests (a)–(c) — dies at sandbox creation, which the runner records
as harness_error trials.

## ASSUMED call shapes (scoring/graders agents: please match or ping)

Only export **names** were specified for `src/scoring`; the runner calls them as:

```ts
buildTrialResult(opts: {
  scenario: Scenario; trialIdx: number; runId: string;
  status: TrialResult['status']; verdicts: Verdict[];
  costUsd: number; simDays: number; wallMs: number; deadlockDiagnosis?: string;
}): TrialResult   // expected to derive items (buildItemVerdicts) + decay internally

buildScenarioVerdict(opts: {
  scenario: Scenario; trials: TrialResult[]; config: EvalSetConfig;
}): ScenarioVerdict

buildSuiteResult(opts: {
  config: EvalSetConfig; subject: SuiteResult['subject'];
  startedAt: string; finishedAt: string; scenarios: ScenarioVerdict[];
}): SuiteResult   // expected to compute green/greenDetail (suiteGreen) internally
```

`gradeTrial(record, { judgeMode: 'cache-only' | 'live' })` — the opts bag is only
passed when a judgeMode was requested; replay always passes `cache-only`.

`buildItemVerdicts`, `decayStats`, `suiteGreen` are not called directly by the
runner — they are assumed to be internal to the three builders above.

## Decisions / workarounds (no contract file was blocking; these are seams)

1. **Quarantine**: `scenarios/quarantine.yaml` is parsed by a minimal built-in
   YAML-subset parser (`{quarantined: [...]}` block or inline list; comments,
   quotes, and `- id: x` rows tolerated) — the project has no YAML dependency.
   Quarantined scenarios still run; after scoring, the runner overrides their
   status to `'quarantined'` (advisory) so scoring's green logic excludes them.
2. **Answer-key hash**: verified as sha256 of the raw file bytes, with a
   fallback to sha256 of `JSON.stringify(JSON.parse(raw))`; `sha256:` prefixes
   are tolerated. Mismatch **warns** in `loadAnswerKey` (per spec) and is a
   hard **PROBLEM** in `lint`. Corpus hashes match raw bytes today.
3. **Answer-key path resolution**: relative `answerKeyRef.path` is tried against
   the scenario file's dir, then `scenarios/`, then `scenarios/keys/<basename>`.
4. **USD cap**: checked against cumulative cost before every trial (shared
   across the concurrency pool). A scenario admitted but capped before trial 0
   becomes `skipped_budget` (built literally, without calling scoring); a
   scenario capped mid-trials is scored on the trials that ran, with a note in
   passDetail. Cheapest-first admission = sort by `budgets.usd` ascending.
5. **Trial status mapping**: `guardTripped != null → guard_tripped`, else
   `deadlock → deadlock`, else terminal_outcome `budget_exhausted →
   budget_exceeded` (when `onExhaustion: 'fail'`), else `completed`. Deadlock /
   guard-tripped runs are still graded (partial credit).
6. **Replay status inference**: no RunReport exists in replay, so status comes
   from the log: terminal_outcome present → completed / budget_exceeded; absent
   → deadlock. `guard_tripped` is NOT reconstructible from events — a
   guard-tripped original replays as its event-visible status. Verdict statuses
   (what test (d) compares) are unaffected. Replay `subject` is
   `{kind: 'scripted', label: 'replay'}` (schema has no 'replay' kind).
7. **gateReport** is captured via `instance.gateReport()` **before**
   `destroy()`; replay uses the contract's empty default
   `{neverRaised: [], unexpected: [], resolutions: []}`.
8. **Suite report threshold line**: `SuiteResult` doesn't carry eval-set
   thresholds, so `renderSuiteReport`/`writeSuiteReport` take an optional
   `{itemFloor}` opts bag (CLI passes `config.thresholds.itemFloor`; default
   0.5 when rendering from a bare suite-result.json without `--set`).
9. **Lint KeyRef convention** (matches the generated corpus): `item.<field>`
   refs are item-relative — resolved against perItem subtrees with the prefix
   stripped; other refs resolve against the key root, `facts`, or any perItem
   subtree. The corpus never uses `kind: 'checklist'` asserts (checklists are
   inlined as artifact_field asserts), so lint's checklist check reports 0 refs.
10. **CLI lazy imports**: `run`/`replay`/`dry-run` import the runner
    dynamically, so `lint` and `report` work before the remaining siblings land
    (verified: `lint --set scenarios/sets/renewal-prep-v1.json` is clean, exit 0).
11. **`--subject baseline`** maps to
    `createBaselineRuntimeFactory({})` with label 'baseline' — the opts shape
    for that factory was not specified; adjust the call in `src/cli/index.ts`
    (cmdRun) if it needs real options.
12. **dry-run** uses `trialsOverride: 1` + `judgeMode: 'cache-only'` (it is an
    authoring gate, not a stats run) and writes to `results/dry-run-<ts>/`.

## Verification status (final, all siblings landed)

- `npx tsc --noEmit`: **clean project-wide** (exit 0).
- `node --test --experimental-strip-types tests/runner.test.ts`: 3 pass / 2 fail:
  - (b) violator-no-gate → not green — PASS (currently trivially: see below).
  - (c) violator-skips-items → scenario failed — PASS (same caveat).
  - (e) suite + rehearsal report rendering — PASS.
  - (a) golden green — **FAIL**, blocked solely by the corpus↔sandbox
    `contentFile` mismatch above: `service.create` throws in `seedFromPack`,
    the runner records a harness_error trial with an error verdict, and the
    scenario correctly fails. Once one side moves, (a) should exercise the real
    pipeline with zero runner changes.
  - (d) replay determinism — FAIL as a knock-on of (a): the golden run dies
    before writing events/world artifacts, so there is nothing to replay.
  - Caveat on (b)/(c): today they pass through the harness_error → invariant
    path (error is never green — behaving as designed); after the pack fix
    they will exercise the real violator executors.
- CLI spot-checks performed:
  - `lint --set scenarios/sets/renewal-prep-v1.json` → clean, exit 0 (validated
    `item.*` KeyRef convention against the real key).
  - `run --set … --subject scripted:golden --filter renewal-golden-3` →
    NOT GREEN, exit 1, matrix + greenDetail printed, suite-result.json and
    suite-report.html written (error path end-to-end).
  - `report --result … --set … --rehearsal renewal-golden-3 --trial 0` over a
    fabricated records dir → both HTML files written; SVG chart with the
    set's itemFloor, gate row with approver, and $/verified-item all present.
