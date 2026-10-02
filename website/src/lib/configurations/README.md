# Configurations workspace

Data layer for `/app/configurations`, the workspace's only area. Four pages share one
top bar with breadcrumbs (no sidebar):

| Page | Route | Shows |
| --- | --- | --- |
| Configurations | `/app/configurations` (+ `/new`) | One equal-size card per configuration; create one in a single form. |
| Configuration | `/[configId]` | Four tiles, the live device's telemetry, the robots; the specification under Details. |
| Robot | `/[configId]/robots/[robotId]` | Test robots: evals. Live devices: telemetry and traces. Details tab. |
| Eval | `…/evals/[runId]` | Metrics, slices and rollouts; a rollout with a real episode opens the replay (`?rollout=`). |

`/app/applications` (and the `/portal`, `/app/device` and `/console` redirects to it)
stays reachable on the same session, but Configurations never links to it.

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
  - `EvalRun.recordedEvaluationId` (`eva_…`): a stored run whose metrics and rollouts
    come from that evaluation. `EvalRun.recordedEpisodeId` (`epi_…`): a one-episode run.
  - `Rollout.episodeId` (`epi_…`): a stored rollout backed by a real episode.
  A rollout is replayable only with a real episode id; there is no simulated replay.
  The replay plays the episode's recorded frames in a bottom sheet with
  `components/console/EpisodeReplay.tsx`, the same player as the console's
  evaluations, and says "No recording for this episode." when there are none.
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

## Tests

- Unit: `npm run test:configurations` (selectors, runs, create, mutations, client
  contract, validation, live poller, sample).
- End to end: `tests/e2e/configurations*.spec.ts`, every API mocked
  (`tests/e2e/support/configurations.ts`, documents API in `support/documents.ts`).
  `configurations-journeys.spec.ts` records video, a trace and milestone screenshots of
  three journeys.
- Live: `npm run test:live` runs `tests/live` against a deployed site. It is skipped
  unless `CONVOY_LIVE_BASE_URL`, `CONVOY_LIVE_EMAIL` and `CONVOY_LIVE_PASSWORD` are set;
  the write journey runs only with `CONVOY_LIVE_ALLOW_WRITES=1` and restores the
  document it found. Artifacts go to `CONVOY_LIVE_OUTPUT_DIR` (default
  `test-results/live`).
