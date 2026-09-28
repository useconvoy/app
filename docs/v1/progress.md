# Implementation progress

The goal remains the complete deploy–execute–evaluate workflow, including compatible learned inference and the intended local/cloud pairing. The simulator baseline and management plumbing are checkpoints, not completion of that goal.

## September 27–28, 2026

- Merged PR #70: existing checks also run on relevant `v1` integration pushes.
- Merged PR #71: pinned MuJoCo/MetaWorld manipulation reference, recorded outcomes, scripted and zero-action baselines. Two existing test timing races were fixed; all remote checks passed before merge.
- PR #72: independent inference worker, versioned dependency-free contracts, mission-bound authorization, artifact/runtime verification, no queued stale decisions.
- PR #73: authenticated projects/robots/applications, immutable releases, deployment generations, mission admission/cancellation, bounded immutable episode summaries, additive schema upgrade preserving legacy records.
- Coordinator/pipeline slice: outbound management and direct inference, original deadline and identity validation, exclusive executor ownership, durable command journal, explicit unknown recovery, managed simulator adapter, reproducible three-process acceptance harness.

Local evidence at the coordinator checkpoint: 195 server tests passed and 5 skipped; 23 focused contract/worker/coordinator tests passed; 8 physics tests passed. The real-process acceptance completed 500 physics steps through the HTTP worker, then passed redeployment plus idle restart, cancellation after at least ten applied actions, and process termination after applied actions. Restart reported unknown, did not add applied commands, and the API rejected conflicting work. CI independently repeats the applicable checks against each published commit.

The policy remains scripted and reads privileged simulator state. The simulator runs in offline lockstep. No result here demonstrates a trained VLA, a paired System 1/System 2 architecture, physical hardware timing, WAN tolerance, or a deployed cloud service.

## Remaining implementation

| Area | Current state | Next evidence needed |
|---|---|---|
| Console workflow | Under construction against actual APIs | Browser login → select release → deploy → ready → start → episode, plus cancellation |
| Learned inference | Runtime factory boundary exists | Public/partner checkpoint with exactly compatible observations/actions, successful closed-loop run and failure comparison |
| Component pairing | Single bounded action-policy profile | Explicit local/remote component manifest, compatible pair activation and timing qualification; no universal plug-and-play claim |
| Release evaluation | Baseline simulator measurements | Persisted evaluation suite/report tied to immutable release/environment, comparison and promotion gate |
| Durable hosting foundation | Single-installation SQLite metadata, local execution journal | Qualified Postgres migrations/concurrency, tenant conversion, jobs/outbox, artifact storage |
| Provider and hosting | Local CPU only | Reproducible service images and infrastructure plan, account/region/budget, provisioned endpoint, hosted acceptance |
| Networking | Direct HTTP, bounded requests/deadlines, TLS outside loopback | Impairment matrix, bounded mission behavior under management/inference loss, target-site latency measurements |
| Hardware/second embodiment | Simulation only | Partner adapter, real controller authority/recovery, qualified model/hardware pair |

No paid infrastructure has been provisioned. Local development does not require AWS access. Hosted deployment will need a concrete cost plan and configured account/region/budget. The legacy installation-wide APIs remain unsuitable as a multi-customer tenancy boundary.
