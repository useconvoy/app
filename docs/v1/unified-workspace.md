# One workspace for robots, configurations and device evidence

`/app` opens the workspace: **Projects** (`/app/projects`) and **Configurations**
(`/app/configurations`) share one account, sign-in and sign-out. The earlier console
at `/app/applications`, with its Device, Chat, Usage and Traces views, has been
removed. `/app/applications`, `/app/device`, `/portal` and `/console` redirect
permanently to `/app/configurations`; browser Chat is gone. Landing-page links begin
at `/app`.

## Feature map

| Workspace area | Functionality |
| --- | --- |
| Projects | Robots, profiles, fleets, runnable configurations and releases, simulator readiness, deployment, tasks and run results |
| Policy and evaluation tools | On the page of a simulated robot that runs an existing execution profile: simulator enrollment and robot binding, immutable release manifests, fixed-seed suites, comparisons, promotion, deployment gates, missions and episodes |
| Configurations | Saved configurations, their robots and evals; the configured device's live telemetry and inference traces on a robot bound to it |
| Episode playback | Recorded frames, selected skill, policy commands, reward/success and separate wall/simulated timings |

The signed-in user's session is forwarded to the control plane; no operator-token
bridge elevates a viewer's role. Revocation and sign-out apply to the whole workspace.
The API's existing device permissions are installation-wide; application resources
retain project ownership. The one configured physical device is explicitly
workspace-wide, not implicitly attached to a selected robot. Live device evidence
does not qualify physical robot actuation or a policy's timing. The
[device API guide](../../website/src/lib/portal/README.md) covers the device API and
its permission behavior.

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
for both workspace and device requests. HTTP is explicitly allowed only to the
named `control-plane:8080` service on that private single-host network; other
remote origins still require HTTPS. The public mutation origin defaults to the
existing `PORTAL_PUBLIC_ORIGIN` unless `CONVOY_CONSOLE_ORIGIN` is provided.
No cloud simulator or model is provisioned by this change. Remote CPU/planner
trials and any GPU service remain separate operator actions.

## Validation and next experiment

Keep the workspace browser regressions and platform proxy tests. The
API tests cover recording-to-episode identity, frame bounds, missing recordings,
unauthenticated access and another project's access. Verify a learned-policy
mission through the UI and watch its resulting recording without a manual export.

The next policy experiment should use a small versioned scenario catalog inside
`integrations/simulation`, sharing the existing MetaWorld/MuJoCo adapter. First
measure a seed baseline, then compare a bounded feedback planner with a rules
baseline using the same observations and retry budget. Add delay, dropout and
stale-response cases. Feedback/retry semantics require a new execution contract;
the current release still selects one fixed skill before policy execution.
