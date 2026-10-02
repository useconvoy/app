# Configurations workspace

Data layer for `/app/configurations`. Configurations and Projects (`/app/projects`) share
one session (`WorkspaceRoot` in `/app`'s layout) and one slim top bar: wordmark,
breadcrumbs, the two areas, and an account menu that shows initials, never the address,
and holds Sign out. Neither area has a sidebar; a project's sections are tabs under its
title. The Configurations journey is four pages:

| Page | Route | Shows |
| --- | --- | --- |
| Configurations | `/app/configurations` (+ `/new`) | One equal-size card per configuration; create one in a single form. |
| Configuration | `/[configId]` | Four tiles, the live device's telemetry, the robots; the specification under Details; the robot turning beside its facts. |
| Robot | `/[configId]/robots/[robotId]` | Test robots: evals. Live devices: telemetry and traces. Details tab. |
| Eval | `…/evals/[runId]` | Metrics, slices and rollouts; a rollout with a real episode opens the replay (`?rollout=`). |

The earlier console (`/app/applications`, `/app/device`, `/portal`, `/console`) is gone; those
paths redirect permanently to `/app/configurations`.

**Entry.** The server redirects `/app` to `/app/projects` (it cannot see the session
cookie, which is scoped to `/api`). A page load that arrives through that redirect goes on
to Configurations once the account's saved workspace has configurations (`entry.ts`);
Projects opened from the top bar or by its URL stays on Projects.

**Projects in the journey.** Runnable project configurations (applications with
immutable releases) appear on Configurations as one row under the cards, only when there
are any. A saved configuration's link to one is a single row under Details (Link, Open,
Change, Unlink in a dialog); it is hidden when there is nothing to link to.

## Data flow

- **Document.** Each account owns one JSON document named `configurations`
  (`ConvoyWorkspace`, schema version 1, `types.ts`). `client.ts` reads it through
  `/api/platform/workspace-documents/configurations`, checks it with `validate.ts`, and
  keeps the document's `revision`. An account's own robots, devices and results live
  only in that document, never in this repository.
- **Writes (documents contract v2).** Every `PUT` names what it replaces:
  `If-Match: "<revision>"` for the stored document, `If-None-Match: *` while there is
  none. Changes show at once and roll back on failure. On `412` the store re-reads the
  document, re-applies the change (an updater function) once and retries. Each write
  has an `Idempotency-Key`, reused only for the identical retried write;
  `saveErrorMessage` explains `409`, `412`, `413`, `428` and `429`. The store re-reads
  on window focus and when the tab becomes visible (at most every 10 s, never while a
  save is pending).
- **Sample and import.** Without a document (404) the generic sample from `sample.ts`
  is shown (three configurations, test robots only, a "Sample" tag in the top bar).
  The sample is never saved: the first change starts the account's own, empty
  workspace (`emptyWorkspace`) and applies to that. "Import workspace" checks the file
  size (2 MiB), creates the document with `If-None-Match: *`, and asks before it
  replaces a stored one. An invalid or unreadable document shows the sample with one
  short notice, and saving is disabled.
- **Changes** (`mutations.ts`, `create.ts`): create a configuration (name, robot,
  hardware preset, edge and cloud models; the route follows from the models), delete
  one, add a robot (a live device, a simulator, or a simulator with offline evals),
  link a robot's offline evals, remove one.
- **Live devices.** A robot with `deviceId` (a device id, or `CONFIGURED_DEVICE` for
  the workspace's configured device) gets measured telemetry, series and inference
  traces from the existing device endpoints. `live.ts` maps them and runs one shared
  poll (15 s, paused while hidden, backoff on 429/5xx). Freshness is judged on the
  server's clock (the snapshot's `fetched_at`, else the response `Date` header). The
  poller is mounted inside the signed-in session (`ConfigurationsRoot`).
- **Platform evals** (`runs.ts`, `components/configurations/platform.tsx`). Evals and
  rollouts can come from the control plane's real records, read only through the
  existing proxy (`/api/platform/…`, GET allow-list in `src/lib/platform/proxy.ts`):
  - `Robot.projectId` (`prj_…`): the project's evaluations, and the missions it ran
    outside an evaluation (one episode each), are the robot's evals, newest first,
    labelled "Eval N" after any stored runs. `Robot.platformRobotId` (`rob_…`, optional)
    keeps only that platform robot's.
  - `Robot.offlineEvaluationIds` (`oev_…`): offline evaluations the robot shows as evals,
    numbered with the platform's and tagged "Offline sim": episodes recorded outside the
    hosted runner and imported unsigned (`docs/v1/offline-evaluations.md`). The eval page
    shows their metrics and rollouts; the replay plays the uploaded frames (PNG or JPEG)
    with one label per action value. Add robot → "Simulator · offline" links them; a
    simulator's Details → Offline evals changes the links. An offline evaluation belongs
    to the configuration whose robots link it: both pickers offer only this
    configuration's and unassigned ones (`offlineEvaluationsFor` in `selectors.ts`), a
    robot keeps its own links, and `addRobot` and `linkOfflineEvaluations` refuse one
    that another configuration's robot links, so a stale form cannot take it.
  - `Robot.offlineEvaluationProvenance` (`{ "oev_…": EvalProvenance }`, optional): how each
    linked offline evaluation ran, as the account declares it, since an import's labels need not
    say. Fields `run`, `perception`, `control`, `planner`, `runner`, `transport`, each optional,
    one line of at most 80 characters. The eval shows them as one line under its title
    ("MuJoCo planner run · Simulator-state perception · Scripted IK · Qwen on Jetson (real calls)
    · Runner: Mac · Transport: …") and under Details, the dashboard's evidence row shows its
    eval's, and the robot's evals list shows each runner. Declared, never checked by Convoy.
    Keys must be offline evaluation ids; an entry for one the robot does not link is a warning
    and is ignored, and `linkOfflineEvaluations` drops the entries of the links it removes.
  - `EvalRun.recordedEvaluationId` (`eva_…`): a stored run whose metrics and rollouts
    come from that evaluation. `EvalRun.recordedEpisodeId` (`epi_…`): a one-episode run.
  - `Rollout.episodeId` (`epi_…`): a stored rollout backed by a real episode.
  A rollout is replayable only with a real episode id; there is no simulated replay.
  The replay plays the episode's recorded frames in a bottom sheet with
  `components/configurations/EpisodeReplay.tsx`, the same player as Projects' runs and a
  robot page's policy tools, and says "No recording for this episode." when there are none.
- **Robot preview.** `RobotSpec.preview` names a simulated robot the website ships, by id
  (`ROBOT_PREVIEW_IDS`: `bimanual-station`); `previews.ts` maps the id to its committed files
  under `public/sim/<id>/` (a VP9 and an H.264 turntable loop and a WebP still, rendered by
  `integrations/simulation/scripts/render_robot_preview.py`). The validator accepts only those
  ids or null, never a URL. The configuration page then shows a "Robot" section below the tabs:
  the loop (muted, looping, inline, with a pause button; the still alone under reduced motion)
  beside the robot, its body and cameras, the edge hardware, models and status, the two cards
  equal in height (stacked below 900px). Without a preview the section is left out:
  its facts alone would repeat Details. The sample's robot uses it; the New configuration form
  sets none.
- **Reading.** Pages use `useWorkspace()`, the selectors in `selectors.ts` (re-exported
  by `client.ts`), `runs.ts` and `status.ts` for evals, results and robot status,
  `format.ts` for numbers and times (UTC), and `routes.ts` for URLs.

## Displayed health

One rule decides a live robot's health everywhere: an open attention flag shows as
`attention`; otherwise an offline device shows `offline`; otherwise warnings show as
`degraded`; otherwise the stored or measured health. `displayHealth` and
`attentionRobots` in `selectors.ts` implement it. A robot without a device shows
whether an eval is running on it (`status.ts`).

## Measured values

Measured values come only from a connected device and are never stored in the
document: the validator rejects them, and a robot with a live binding stores no
telemetry. A missing value reads "Not reported" (or a dash in a table), never 0.

## Evidence panels

Two panels, tagged "Measured", from recorded values only (`evidence.ts`, pure, unit-tested;
components in `components/configurations/evidence/`, styles in `src/styles/evidence.css`):
on the eval page (Overview) for an offline eval whose episodes report planner and safety
metrics, and compact on the configuration dashboard (its newest such eval, else the live
device's latest requests). The metric names are the simulator's
(`integrations/simulation/…/bimanual_pill_task/offline_replay.py`, `device_metrics`).

- **Latency budget** of one planner decision, at p50 and p95, as stacked bars on one linear
  axis from 0: prefill ≈ time to first token (`planner_ttft_p50_ms`); decode ≈ on-device
  latency − first token (`planner_device_*_ms`); network / relay ≈ end-to-end round trip −
  on-device latency (`planner_e2e_*_ms`). The formulas apply to the percentile values, so the
  segments add up to the end-to-end figure; a negative difference is not drawn. An eval's figure
  is the median, across the episodes that report it, of each episode's own percentile. No
  first-token p95 is recorded, so the p95 bar shows on-device time unsplit (striped). Live spans
  have no end-to-end time: network / relay is Not reported there. Small facts: tokens in / out
  (p50), the prefill share of on-device time, and the slowest episode's end-to-end p95.
- **Latency targets.** `Configuration.latencyTargets` (`{ label, ms }[]`, at most 6, labels unique
  ignoring case, 0 < ms ≤ 600 000) are drawn as labelled reference rows on the same axis.
  They are declared, never measured; the sample declares "Teleop · near" 60 and "Teleop · far" 120.
- **Autonomy.** An intervention is a recorded event where an operator would take over:
  a failed decision (`planner_failed_decisions`: no usable reply after the decision's retries),
  a protective stop (`protective_stops`: the simulator stops an arm when a contact force exceeds
  its safety threshold, 10 N against the other arm, the bottle or the cap and 60 N against the
  table, so an arm–arm contact above the threshold is counted here), or an unfinished episode
  (outcome timeout, failure or safety stop; one per episode). `arm_arm_contacts` counts arm–arm
  contacts at any force: it is shown "Not counted", since it has no threshold and the contacts
  above it are already protective stops. Decisions are resolved decisions: `planner_decisions`
  where the export reports it, else `planner_valid_replies + planner_failed_decisions`, the same
  count (a valid reply ends a decision; a decision still open when the episode ended is not counted).
  - Autonomous episodes: episodes with no intervention (count and share).
  - Interventions per episode: interventions ÷ episodes; per 100 decisions: 100 × interventions ÷ decisions.
  - Decisions between interventions: decisions ÷ interventions (none when there were no interventions).
  - Accepted on the first call: Σ `planner_first_call_accepted` ÷ Σ `planner_decisions`, the
    simulator's decision-level counts (exported since its `platform-chat-v1` transport, with
    `planner_reasked_decisions`: decisions = first-call accepted + re-asked). Every episode must
    report both, consistently; earlier evals do not, so they read Not reported.
  - By type: events and episodes per type.

  A count an episode does not report makes every total it feeds Not reported, never 0; an episode
  with a reported intervention is not autonomous whatever else is missing. Definitions are behind
  each panel's info toggle. End-to-end tests: `tests/e2e/configurations-evidence.spec.ts`.

## Slices and planner calls

An offline eval's episodes form slices when they report two or more `slice` texts (`slices.ts`,
pure, unit-tested). A slice is the simulator's eval slice, e.g. `nominal` or `pill_count_30`, shown
"Nominal" and "Pill count 30". Slices ran under different conditions, so their results are never
pooled into one rate:

- **Eval page.** One success tile per slice ("Success · Nominal", 3/5), then Episodes and Median
  time, labelled "all slices". The Slices table puts the slices side by side: success, seeds, pills
  placed, failed decisions, planner calls by result, on-device and end-to-end p50 and p95 per
  decision, median steps and time. Metrics has one mean per slice, and Rollouts a Slice column. The
  evidence panels show one slice at a time, chosen in a segmented control (`?slice=`, the first
  slice by default) and named in each panel's scope line.
- **Dashboard, configuration card, robot page.** The newest scored eval's success per slice
  ("Nominal 3/5 · Pill count 30 0/5"). An offline eval's slices need its episodes, so these wait
  for them rather than show a pooled rate first. The robot page's tiles describe its newest eval
  only, since one robot's evals may have run differently; its evals list names each import, its
  declared runner and its date.
- **Planner calls by result** (Autonomy panel, Slices table, Metrics): a valid reply; a reply the
  executive refused (`planner_invalid_choice`, `planner_invalid_json`, `planner_invalid_schema`:
  the model answered and the answer was not usable); a device or transport failure
  (`planner_device_errors`, `planner_http_errors`, `planner_timeouts`: no answer came back). Both
  unusable kinds are always shown, a 0 as 0; a part an episode does not report makes its total
  Not reported.

End-to-end tests: `tests/e2e/configurations-slices.spec.ts` (two evals of one simulator, the same
task run from two machines).

## Tests

- Unit: `npm run test:configurations` (selectors, runs, create, mutations, client
  contract, validation, live poller, sample, evidence, slices).
- End to end: `tests/e2e/configurations*.spec.ts`, every API mocked
  (`tests/e2e/support/configurations.ts`, documents API in `support/documents.ts`).
  `configurations-journeys.spec.ts` records video, a trace and milestone screenshots of
  three journeys.
- Live: `npm run test:live` runs `tests/live` against a deployed site. It is skipped
  unless `CONVOY_LIVE_BASE_URL`, `CONVOY_LIVE_EMAIL` and `CONVOY_LIVE_PASSWORD` are set;
  the write journey runs only with `CONVOY_LIVE_ALLOW_WRITES=1` and restores the
  document it found. Artifacts go to `CONVOY_LIVE_OUTPUT_DIR` (default
  `test-results/live`).
