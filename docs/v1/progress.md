# Implementation progress

The goal remains the complete deploy–execute–evaluate workflow, including compatible learned inference and the intended local/cloud pairing. The simulator baseline and management plumbing are checkpoints, not completion of that goal.

## September 27–28, 2026

- Merged PR #70: existing checks also run on relevant `v1` integration pushes.
- Merged PR #71: pinned MuJoCo/MetaWorld manipulation reference, recorded outcomes, scripted and zero-action baselines. Two existing test timing races were fixed; all remote checks passed before merge.
- PR #72: independent inference worker, versioned dependency-free contracts, mission-bound authorization, artifact/runtime verification, no queued stale decisions.
- PR #73: authenticated projects/robots/applications, immutable releases, deployment generations, mission admission/cancellation, bounded immutable episode summaries, additive schema upgrade preserving legacy records.
- Coordinator/pipeline slice: outbound management and direct inference, original deadline and identity validation, exclusive executor ownership, durable command journal, explicit unknown recovery, managed simulator adapter, reproducible three-process acceptance harness.

Local evidence at the coordinator checkpoint: 195 server tests passed and 5 skipped; 23 focused contract/worker/coordinator tests passed; 8 physics tests passed. The real-process acceptance completed 500 physics steps through the HTTP worker, then passed redeployment plus idle restart, cancellation after at least ten applied actions, and process termination after applied actions. Restart reported unknown, did not add applied commands, and the API rejected conflicting work. CI independently repeats the applicable checks against each published commit.

That coordinator checkpoint used a scripted policy and privileged simulator state. Subsequent learned-policy evidence is listed below. All current simulation runs use offline lockstep; they do not establish physical hardware timing, WAN suitability or a deployed cloud service.

## Remaining implementation

| Area | Current state | Next evidence needed |
|---|---|---|
| Console workflow | PRs #75, #82 and #84 merged; deployment, evaluation, comparison, promotion and paired display verified against actual services | Repeat the workflow with independently activated candidate components |
| Learned inference | PR #78 merged; actual pretrained SmolVLA camera-policy evidence through managed missions | Extend the qualified task/seed envelope and qualify target compute |
| Component pairing | PRs #83–#85 merged; controlled and actual Qwen admission plus SmolVLA/MuJoCo qualified locally; paired console evaluation verified | Live Jetson qualification and deployment-driven component activation; no universal plug-and-play claim |
| Release evaluation | PRs #80 and #82 merged; suites, leased jobs, comparison and promotion gates qualified locally and through the browser | Paired evidence and broader model/task qualification |
| Durable hosting foundation | PostgreSQL migrations and concurrency checks, restartable evaluation jobs, local execution journal | Full tenant conversion, artifact storage/retention and database restore rehearsal |
| Provider and hosting | PRs #79 and #81 merged; local service images/TLS/PostgreSQL and reviewed AWS CPU staging configuration | Account/region/budget, provisioned endpoint and hosted acceptance |
| Networking | Direct HTTP, bounded requests/deadlines, verified TLS; six pipeline fault cases qualified | Wider delay/loss/load matrix and target-site latency measurements |
| Hardware/second embodiment | Simulation only | Partner adapter, real controller authority/recovery, qualified model/hardware pair |

No paid infrastructure has been provisioned. Local development does not require AWS access. Hosted deployment will need a concrete cost plan and configured account/region/budget. The legacy installation-wide APIs remain unsuitable as a multi-customer tenancy boundary.

## Subsequent verified checkpoints

- PRs #72–#76 are merged into `v1`, including the independent worker, lifecycle
  APIs, coordinator, authenticated console and PostgreSQL foundation. Their
  applicable remote checks passed before merge.
- Merged PR #77 qualifies all six real-process fault cases against a disposable
  PostgreSQL database. The local PostgreSQL and SQLite runs passed; new CI
  execution is currently blocked by the GitHub account's billing/spending state.
- Merged PR #78 runs actual pretrained SmolVLA on camera observations through the
  managed pipeline. Two seed-0 missions on the same warm worker each succeeded
  in 54 actions, matching a separate direct run. This is offline lockstep CPU
  inference, not paired edge/cloud execution or physical robot timing.
  A final clean-tree run against the merged evaluation/services base completed
  in 54 actions and 44.17 seconds, matching the direct action trajectory. Root
  also verified 24 merged lifecycle/evaluation cases, including real PostgreSQL.
- Merged PR #79 runs API, web, scheduler, inference,
  simulator and PostgreSQL as containers with verified internal TLS. Real
  mission acceptance and persistence across stop/start passed locally.
- Merged PR #80 adds immutable fixed-seed suites, durable leased jobs,
  complete case accounting, comparisons and optional promotion gates. Its
  separate-process acceptance ran four 500-step physics missions across two
  suite executions, recovered after killing the job process without admitting
  another copy of a case, and enforced the promotion gate.
- Merged PR #81 adds reviewed AWS CPU staging infrastructure and a documented
  cost model. Validation used local runtime checks and mocked Terraform plans;
  no cloud resources were created.
- Merged PR #82 adds evaluation, cancellation, comparison and promotion to the
  console. Browser acceptance drove real API/job/simulation processes through
  those actions and a gated mission start.
- PR #83's clean source `00717ab` completed one paired seed-0 mission: a durable
  accepted controlled proposal, 54 actual SmolVLA actions and benchmark success.
  Every action matched the original direct trace. Mission wall time was 44.36
  seconds for 0.675 simulated seconds. The [recorded evidence](../../examples/manipulation/evidence/paired-controlled-seed0.json)
  identifies the deterministic planner explicitly; it does not establish LLM or
  Jetson inference. The initial `14bb784` attempt stopped at planner readiness
  with no mission admitted; the module-entrypoint bug was fixed and covered by a
  subprocess HTTP probe before this successful run.
- The actual container installation upgraded PostgreSQL from `0001_fleet` to
  `0002_evaluations`, preserved its robot/history, and completed two 500-action
  cases through the separate evaluation service. A subsequent mission used the
  current deployment after evaluation advanced its generation. The final
  acceptance-helper-only edit first ran via stdin in the existing image. After
  transient registry DNS failure cleared, the rebuilt reference image also
  completed another successful 500-action mission on the same installation.

The Jetson is intended to be the robot-side edge computer in the future paired
architecture. After the user powered it on, the recorded hostname resolved and
its saved SSH host identity matched. Its DHCP address had changed. The available
local SSH key was rejected; device login is pending. Current hardware/model state
remains unverified. A text LLM is not treated as a
compatible arm action policy. Compatible edge inference, a separate cloud planner,
paired activation, cloud hosting, complete tenancy and artifact/retention work
remain outstanding. No paid cloud resources have been provisioned.

PR #85's clean source `c7d9166` completed actual Qwen text-model admission in
897 ms followed by 54 SmolVLA actions and the benchmark success signal. All
actions matched the direct seed-0 trace; active wall time was 46.34 seconds for
0.675 simulated seconds. The [record](../../examples/manipulation/evidence/paired-qwen-local-seed0.json)
also preserves earlier readiness and sleep-interrupted failures. All inference
ran on the developer Mac CPU; this does not qualify Jetson/cloud placement.

An implementation audit confirmed that selecting a deployment currently checks
already configured worker/planner identities. It does not itself replace their
model processes. The next activation slice must stage a registered local recipe,
switch at an idle boundary, verify observed component identities, and support an
explicit restore to a prior bundle. Persistent packaging alone does not close
that gap.

PR #86 adds component/stage diagnostics to durable terminal episodes and the
console. The full agent suite passed (227 passed, four skipped), with 37 focused
checks on its final source and browser acceptance through a 500-action scripted
MuJoCo mission and cancellation.

PR #87 adds the optional foreground process ownership prerequisite. Thirteen
focused checks passed on both Darwin and Linux, including an owner killed before
PID persistence, exact child recovery, and an untouched unrelated sentinel.
PR #88 connects it to the real model supervisor. Clean source `a0d3bc5` loaded
actual Qwen, served one fixed-skill request, survived an owner kill with the native
child left alive, verified that orphan's exit, and loaded a second configuration.
Both requests succeeded and all owned resources stopped. Deployment-driven paired
activation and explicit restore remain outstanding.
