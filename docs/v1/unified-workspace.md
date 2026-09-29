# One workspace for device inference and robot applications

`/app` opens `/app/applications`. The shared navigation reaches `/app/device`
without leaving the application. `/console`, `/portal`, and portal `?view=` links
remain working entry points.

## Feature map

| Workspace area | Existing functionality |
| --- | --- |
| Robot applications | Projects; simulator enrollment and robot registration; immutable release manifests and component identities; deployment readiness; mission start/cancellation and history |
| Release qualification | Fixed-seed suites, candidate/baseline comparisons, promotion, application deployment gates and evaluation cancellation |
| Episode playback | Actual recorded camera frames, previous/next/seek/play, selected planner skill, four policy commands, reward/success and separate wall/simulated timings |
| Device & inference | Physical-device status and telemetry, model chat, measured usage and request traces |

The shared shell does not combine authentication authorities. Management uses the
caller's Convoy session and existing project ownership checks. The device demo
uses its existing restricted demo session for one configured physical device.
Device credentials cannot enroll robots or start deployments. Local simulator
credentials do not claim that the physical Jetson is connected. A single-account
physical-device management experience requires an explicit device permission model;
this change does not replace that with a privileged token bridge.

## Recordings

The Python API has a small read-only journal adapter in
`services/replay.py`. Configure an absolute journal path with
`CONVOY_REPLAY_JOURNAL` for a single simulator, or `CONVOY_REPLAY_JOURNALS`
as a JSON map from robot IDs to absolute journal paths for multiple simulators.
The local activation harness configures the single journal automatically.

`GET /api/v1/episodes/{id}/replay` returns metadata after verifying terminal report,
full execution identity, consecutive applied commands and observation continuity.
`GET /api/v1/episodes/{id}/replay/frames/{index}` returns one bounded PNG and its
associated action/result. Index zero is the initial observation. Both routes first
check the same episode ownership as the existing episode endpoint. The browser
uses the session-bound, no-store platform proxy. Paths never come from the client.
Frame requests are incremental: the browser does not download an entire video or
put camera recordings in the public website bundle.

No database migration, new worker, object store, model process, or GPU is needed.
The coordinator remains the sole journal writer; playback cannot apply commands.
For remote simulators, the API currently needs a read-only mount or consistent
snapshot of their journal. Arbitrary remote recording upload, retention policies,
and live video are not implemented. Missing recordings leave episode outcomes
and technical details available. Non-camera profiles have no camera playback.

Playback pace is a viewing setting. The present CPU experiment advances physics
in lockstep with inference; its simulated duration is not a real-time guarantee.
MetaWorld benchmark success is not proof of stable physical placement.

## Hosting

The existing Lightsail deployment still consists of the same Next.js service and
Python API. Its Compose configuration now supplies the management API connection
as well as the existing portal connection. HTTP is explicitly allowed only to the
named `control-plane:8080` service on that private single-host network; other
remote origins still require HTTPS. The public mutation origin defaults to the
existing `PORTAL_PUBLIC_ORIGIN` unless `CONVOY_CONSOLE_ORIGIN` is provided.
No cloud simulator or model is provisioned by this change. Remote CPU/planner
trials and any GPU service remain separate operator actions.

## Validation and next experiment

Keep the existing portal browser regressions and platform proxy tests. The new
API tests cover recording-to-episode identity, frame bounds, missing recordings,
unauthenticated access and another project's access. Verify a learned-policy
mission through the UI and watch its resulting recording without a manual export.

The next policy experiment should use a small versioned scenario catalog inside
`integrations/simulation`, sharing the existing MetaWorld/MuJoCo adapter. First
measure a seed baseline, then compare a bounded feedback planner with a rules
baseline using the same observations and retry budget. Add delay, dropout and
stale-response cases. Feedback/retry semantics require a new execution contract;
the current release still selects one fixed skill before policy execution.
