# Offline evaluations

Simulation episodes recorded outside the hosted runner can be imported and replayed in
Configurations. An import is its owner's own record: Convoy did not run it, and nothing in it is
signed or checked against a release. The API says so on every evaluation (`source: "offline"`,
`signed: false`, `scope`), and the website tags each one "Offline sim".

## API

All routes are scoped to the signed-in account; administrators cannot read or change another
account's imports. Reads need any role, writes the operator role (re-checked inside the write).

| Method | Path | Answer |
|---|---|---|
| `GET` | `/api/v1/offline-evaluations` | `{items: [evaluation]}`, newest first |
| `POST` | `/api/v1/offline-evaluations` | `{name, task, config_label, policy_label}` → 201 evaluation |
| `GET` | `/api/v1/offline-evaluations/{oev}` | evaluation + `episodes` |
| `DELETE` | `/api/v1/offline-evaluations/{oev}` | 204; its episodes and files go too |
| `POST` | `/api/v1/offline-evaluations/{oev}/episodes` | episode upload → 201 episode (200: identical content already stored) |
| `DELETE` | `/api/v1/offline-evaluations/{oev}/episodes/{oep}` | 204 |
| `GET` | `…/episodes/{oep}/replay` | the hosted replay manifest shape, plus `evaluation_id`, `action_dim`, `action_labels`, `images` |
| `GET` | `…/episodes/{oep}/replay/frames/{i}` | the hosted frame shape, plus `image_media_type` and `image_index` |

An evaluation is `{id, name, task, config_label, policy_label, summary, source, signed, scope,
created_at, updated_at}`. The server computes `summary` from the episodes: `episodes`, `successes`,
`success_rate`, `median_steps`, `median_wall_seconds`, `median_sim_seconds`, `stored_bytes`.

An episode upload is the replay format in one JSON body: `seed`, `outcome` (`success`, `failure`,
`timeout`, `safety-stop`), optional `metrics` (≤32 lower_snake_case names → number, boolean, text
≤200 or null; ≤4 KiB), `action_labels`, `skill`, `planner_ms`, `wall_seconds`, `sim_seconds`, and
`frames`: `{index, image_png_base64, action, reward, success, policy_ms}` for index 0…steps. Frame 0
is the camera before the first action (no action); frame k follows action k. Every action has the
same width, 1–64 finite values. `image_png_base64` holds a PNG or a JPEG; a frame without one replays
the latest earlier image (`image_index` names it). The frame field keeps its hosted name for one
player; `image_media_type` gives the actual type.

A frame may also carry a reported `hierarchy` snapshot from an asynchronous planner experiment:
`planner_state` (`idle`, `pending`, `accepted`, `stale`, `error`), `task_revision` (integer
0–2³¹−1), `active_skill` (printable text ≤64), optional `target` (printable text ≤24 or null), and
optional `planner_latency_ms`, `observation_age_ms`, `physics_lag_ms` (finite 0–86,400,000 or null).
The snapshot belongs to that exact physics tick, including frame 0; it is independent of a repeated
camera image. Unknown fields are rejected. Existing uploads and recordings without it keep their
original replay shape and content identity.

Use `metrics.measurement_source` to describe the experiment's measurement clock and runner. It is
shown in the replay and evaluation details. Replay metadata adds `has_hierarchy: true` when any frame
has a snapshot, plus that reported source when provided. The player shows skill selection, targets,
planner state, and local timing alongside applied actions. Aggregate numeric metrics are reported
means per episode; importing them does not verify robot timing or qualify a cloud deployment.

Writes follow the workspace-document hardening: authentication, role, client header, ownership,
`Idempotency-Key` and the write budget are checked before the body is read; receipts keep metadata
only (24 h, newest 1024 per account); the audit log records labels, ids, counts and sizes, never
frames.

## Limits

| What | Limit |
|---|---|
| Upload body | 16 MiB (refused while it arrives; 120 s to arrive) |
| Steps | 1–2048 |
| Images per episode | 512; PNG or baseline/progressive JPEG, ≤320 px a side, ≤256 KiB each |
| Action width | 1–64 values, each within ±1e6 |
| Episodes per evaluation | 200 |
| Evaluations per account | 100 |
| Writes | 120 per 10 minutes per account (429 with `Retry-After`) |
| Storage | the shared recording quota (`CONVOY_RECORDING_QUOTA_BYTES`, 512 MiB) and 128 MiB per account (`CONVOY_OFFLINE_EVALUATION_QUOTA_BYTES`); 507 when full |

Images are checked without an image library: PNG chunks, CRCs and a bounded inflate of exactly the
declared scanlines; JPEG markers up to the frame header and a final EOI.

## Storage

Each episode is one server-built SQLite file, `recordings/<oep>.sqlite3`, beside hosted recordings,
under their lock and their installation-wide quota, so hosted uploads count offline files and offline
uploads count hosted ones. Unlike hosted recordings, offline ones can be deleted. A file is written as
`.partial` inside the write transaction and published after the commit; removal renames it to
`.removed` before the rows go. The next offline write settles anything an interruption left.

Only new recordings with hierarchy snapshots have an additional `frame_metadata` table; older
recording files are read without modification. No control-plane database migration is required.

Rows: `offline_evaluations` and `offline_episodes`, additive under SQLite schema version 5 and
Alembic `0004_offline_evaluations` on PostgreSQL. A release without them still starts on the database.

## Import

`integrations/simulation/scripts/import_offline_eval.py DIRECTORY` (standard library only) reads
`evaluation.json` and one directory per episode (`replay.json` and `frames/<index>.json`, or an image
file beside a frame's JSON) and uploads them. Credentials come from the environment and are never
printed: `CONVOY_SERVER`, then `CONVOY_API_TOKEN` (the API, `/api/v1`) or `CONVOY_EMAIL` and
`CONVOY_PASSWORD` (the website's proxy, `/api/platform`). Reruns reuse their idempotency keys;
`--dry-run` checks sizes without uploading; `--write-example DIR` writes generic frames to try it.

In Configurations: Add robot → **Simulator · offline**, or a simulator's Details → Offline evals.
Episodes that report different `metrics.slice` values are shown per slice, never as one pooled
rate. How an import ran (simulator, perception, control, planner, runner, transport) is declared
on the robot that links it (`Robot.offlineEvaluationProvenance`, see
`website/src/lib/configurations/README.md`); the import's labels need not say.
