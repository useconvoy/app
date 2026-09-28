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
| Console workflow | Through PR #89: independent A/B activation, Start, evaluation, comparison, promotion and restore verified against actual local models | Desired versus observed component display and freshness |
| Learned inference | PR #78 merged; actual pretrained SmolVLA camera-policy evidence through managed missions | Extend the qualified task/seed envelope and qualify target compute |
| Component pairing | Through PR #89: real Qwen admission and SmolVLA/MuJoCo, deployment-driven activation and restore qualified locally | Live Jetson and genuine remote planner qualification; no universal plug-and-play claim |
| Release evaluation | Through PR #89: suites, leased jobs, comparison, promotion and actual paired A/B evidence qualified locally and through the browser | Broader seeds, task and model qualification |
| Durable hosting foundation | PostgreSQL migrations and concurrency checks, restartable evaluation jobs, local execution journal | Full tenant conversion, artifact storage/retention and database restore rehearsal |
| Provider and hosting | PRs #79 and #81: local services and reviewed AWS CPU staging; clean `0674a231` qualifies public-key separation, restart persistence and lost-key refusal in local containers, plus rebuilt AWS wrappers locally | Account/region/budget, provisioned endpoint and hosted acceptance |
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
cloud hosting, complete tenancy and artifact/retention work
remain outstanding. No paid cloud resources have been provisioned.

PR #85's clean source `c7d9166` completed actual Qwen text-model admission in
897 ms followed by 54 SmolVLA actions and the benchmark success signal. All
actions matched the direct seed-0 trace; active wall time was 46.34 seconds for
0.675 simulated seconds. The [record](../../examples/manipulation/evidence/paired-qwen-local-seed0.json)
also preserves earlier readiness and sleep-interrupted failures. All inference
ran on the developer Mac CPU; this does not qualify Jetson/cloud placement.

An audit before PR #89 confirmed that selecting a deployment then checked
already configured worker/planner identities. It did not replace their model
processes. The required follow-up was to stage a registered local recipe, switch
at an idle boundary, verify observed component identities, and support an explicit
restore. PR #89 subsequently closed that local activation gap.

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
Both requests succeeded and all owned resources stopped. At that checkpoint,
deployment-driven paired activation and explicit restore remained outstanding.

PR #89 completed local deployment-driven activation and restore. One installation
passed A → B → A through the harness and additional browser deployment, execution,
paired evaluation, comparison, promotion and restore. Six seed-0 missions each
accepted a real Qwen plan and applied 54 SmolVLA actions matching the direct trace.
Seven bindings and all 21 model process incarnations stopped cleanly. See the
[acceptance record](local-activation.md). This is still local CPU, offline lockstep;
it does not establish multi-seed reliability, Jetson execution or WAN timing.

Merged PR #90 adds API-private Ed25519 mission signing and purpose-scoped
public verification in model services, with rotation, revocation and original
expiry preservation. Clean `20947f9` passed another real Qwen/SmolVLA A → B → A
run: three accepted plans, 162 learned actions equal to the direct reference, and
verified cleanup. See [execution signing](execution-signing.md).

The current hosting slice applies that authority separation to scripted Compose
and unapplied AWS templates: private signing material only for the API, public
action verification only for inference, and no execution keys for jobs. Its
shared key helper passes 28 focused tests, including interrupted initialization
and refusal to overwrite or regenerate an existing installation's keys.

Clean `0674a2312644a9e15d8f6f0126b5aa5a05479812` passed isolated Compose
acceptance: one successful 500-action scripted MuJoCo mission, full down/up with
unchanged private/public key hashes and retained mission history, no automatic
mission start, and a second successful 500-action mission. Independent checks
verified stored JWT signatures at their original issuance time, all 1,000 command-journal
rows, expected read-only key mounts/private file setting, and unchanged identities/start times/PIDs/restart
counts for all eight pre-existing containers. Deleting both disposable key volumes
made the next startup fail without regenerating keys or starting services. Final
cleanup removed only the disposable installation's resources.

Both rebuilt ARM64 AWS wrappers also passed locally: API migrations and runtime
DML, denied DDL, private signer materialization and stable claim/reclaim expiry;
public-only inference admitted a signed action and rejected planner/HMAC grants
and conflicting configuration, then shut down gracefully. See the
[qualification](public-key-hosting.md) and [sanitized receipt](../../examples/manipulation/evidence/public-key-hosting.json).
These are local results. No paid cloud resources were provisioned.

The [Linux ARM64 planner slice](linux-planner-image.md) packages the actual Qwen
runtime independently. Clean `afe128b` passed image inspection, one signed real
proposal in 12.58 seconds, purpose/replay rejection, native loss, startup
interruption and complete cleanup. A deterministic Linux regression also proved
and fixed premature stopped records when a main thread exited before its other
threads. The final local checks were 85 passed with two Linux-only skips, plus
all 15 ownership tests on Linux. Hosted CI could not start due to account billing.
This is planner packaging qualification within a 30-second contract; remote
HTTPS pairing, cloud/WAN and Jetson qualification remain outstanding.
