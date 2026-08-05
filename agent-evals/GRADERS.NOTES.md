# Graders + scoring — contract friction notes

None of the contract files blocked the build; these are the judgment calls and
workarounds where the contracts were silent or in tension. No contract file was
modified.

1. **Verdict carries no `weight`** (src/schema/verdict.ts), but ItemVerdict.q
   is defined as a *weighted* mean. Workaround: `buildItemVerdicts` looks
   weights up by `graderId` from the scenario's grader specs (items[].graders,
   itemGraderTemplate, scenario.graders); unknown graderIds default to 1.

2. **GradeRecord has no runId** (src/sandbox/api.ts), but Verdict.scope
   requires one. `gradeTrial` uses `events[0].missionId`, falling back to
   `scenario.id` for empty logs.

3. **Item identity is two names**: events/tags attribute by the item domain
   key (`ItemSpec.itemId`, used for `event.itemRef` scoping and `{itemRef}`
   interpolation), while `item.X` KeyRefs resolve through `ItemSpec.keyRef`
   into `answerKey.perItem[keyRef]`. The grader ItemCtx carries both.

4. **Cross-team call-shape drift**: src/runner (written concurrently) calls
   `buildTrialResult` / `buildScenarioVerdict` with a single options object,
   while the design specifies positional signatures. Both are supported via
   erasable overloads; the object form also accepts a precomputed trial
   `status` (the runner derives it from events) in place of a RunReport.

5. **'error is never passed' vs pass rules**: an error verdict makes a trial
   fail the at_least count, which would surface as scenario 'failed'. Since
   'error' must mean harness fault (never subject fault), the scorer recounts
   the pass rule treating error verdicts as neutral: subject-genuine misses →
   'failed'; errors-as-the-only-problem → 'error'. Invariant-class errors
   still force 'failed' per the invariant rule.

6. **Advisory verdicts and gating**: advisory (uncalibrated-judge) verdicts
   are structurally excluded from the invariant rule, item status/q, trial
   pass counting, scenario error status, and suiteGreen error blocking; they
   are counted and noted in `suiteGreen` detail. This is the "advisory never
   gates" contract enforced in one place per layer.

7. **`gate:unexpected`** is emitted only when `approvals.mode === 'scripted'`
   with `onUnexpectedGate: 'fail_scenario'`, matching the design; unexpected
   gates under a custom resolver or auto modes produce no synthetic verdict.
