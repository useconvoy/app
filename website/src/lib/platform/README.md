# Application console

`/app` opens `/app/applications`, the shared workspace for robot applications and device connection. `/console` redirects here; `/portal` and `/app/device` redirect to its device section while preserving supported `view` links. All journeys use the same management account and session. Device requests forward the caller's session to the same API, preserving backend roles and request ownership.

Set server-side `CONVOY_API_URL` to the API origin (default `http://127.0.0.1:8080`). HTTP is permitted only for literal localhost addresses; remote origins require HTTPS. Set `CONVOY_CONSOLE_ORIGIN` to the exact public console origin when a reverse proxy changes the request origin. Neither setting is exposed as a browser-selected endpoint.

Sign in with an existing management account. The proxy forwards only that user's `convoy_session`, scopes it to `/api`, and sets HttpOnly, SameSite=Strict, and Secure on HTTPS. The proxy requires exact Origin and `X-Convoy-Client: web` on every mutation, including login/logout, forwards idempotency keys, bounds request/response bodies, and refuses redirects. No device credential or static operator token is required in the web process.

Workspace documents (`GET workspace-documents`, `GET`/`PUT`/`DELETE workspace-documents/{name}`) are each account's own JSON content. The API scopes them strictly to the signed-in user, administrators included, and allows any role. Names match `^[a-z0-9][a-z0-9-]{0,63}$`. `PUT` sends `{"schema_version": n, "body": {…}}`, where the body is a JSON object of at most 2 MiB as compact JSON; an account holds at most 16 documents. `PUT` and `DELETE` require an `Idempotency-Key` and are the only routes with those methods. They are mutations with the same Origin and client-header checks, and only these routes accept request and response bodies up to 2 MiB + 64 KiB. `DELETE` returns `204` with no body.

The console supports project creation, simulated device enrollment and robot binding, application creation, immutable manifest registration, deployment requests, separately authorized mission starts, cancellation requests, and recorded episode summaries. Release qualification adds immutable fixed-seed suites, durable evaluation progress/cancellation, paired report comparison, explicit promotion and application gates. A passing report is not itself a promotion. The authoritative robot reservation blocks ordinary deployment/Start even when an older unresolved evaluation falls outside the bounded history. Qualification is one admission condition alongside role, pause, readiness, active-mission and current-snapshot checks. Start requires the selected release to match the deployed release.

A ready deployment is the last acknowledged state, not evidence of current robot health. Control-plane snapshots refresh every three seconds. Failed qualification reads also disable admission; stale reads for a previous selection cannot qualify a different release. Lost polling does not claim that execution stopped. The coordinator and `convoy-evaluations` job must be running. Externally managed model endpoints must already serve the selected release. The optional [local recipe owner](../../../../docs/v1/local-activation.md) lets ordinary deployment and evaluation requests activate an installed Qwen/SmolVLA pair on one development host. The console does not provision remote compute. Signing out revokes session-backed evaluations' authority for new cases.

New APIs enforce project ownership within one installation. The existing device/enrollment APIs remain installation-wide; do not expose this as a fully isolated multi-tenant service. Registration offers the two Sawyer simulation profiles: state observations and camera observations for SmolVLA. The third paired text-planner/camera-policy option requires the API to explicitly include `metaworld-smolvla-text-skill-v1` in `auth/me` → `installation.execution_profiles`; it stays disabled on an older or unconfigured server. This capability describes backend support/configuration, not component readiness.

The manifest display handles both schema 1 single-policy and schema 2 paired releases. Paired releases show the nested action runtime, planner runtime, fixed task, artifact/protocol identities and declared placement. The supported planner placements are a development Jetson on the LAN or the local controlled fixture. The controlled runtime is explicitly labeled deterministic admission without text-model inference. Robot and suite compatibility uses the top-level paired profile, not the nested visual action profile. Registration records identities; it does not install or provision components or qualify physical hardware. Model names are not inferred from runtime labels or artifact digests.

`pnpm test:platform` checks the proxy's route/scope allowlist, session forwarding, mutation origin checks, body bounds (including the workspace-document allowance), cookie handling and safe errors. `pnpm typecheck`, `pnpm lint`, `pnpm check:tokens`, and `pnpm build` check the application. End-to-end validation must additionally run the browser against the actual API/coordinator/worker; mocked transport tests do not establish the robot lifecycle.

The live browser check is `node scripts/test-live-console.mjs CONNECTION_JSON NEW_OUTPUT_DIRECTORY`.
Run `pnpm build` first and start the private local stack with
`examples/manipulation/pipeline.py --serve`. The check launches its own loopback web
server, verifies a completed 500-step episode through the actual worker and MuJoCo,
then cancellation, session persistence, mobile layout and logout. CI creates and
cleans up that stack automatically; only screenshots and the credential-free
result are published.

`CONVOY_EVALUATION_PYTHON=/absolute/path/to/integrations/simulation/.venv/bin/python
node scripts/test-live-evaluations.mjs CONNECTION_JSON NEW_OUTPUT_DIRECTORY`
adds the evaluation browser flow against the same private SQLite `--serve`
harness. It starts its own job only after demonstrating persistent reservation
and requested cancellation, runs two real two-seed evaluations, compares and
promotes one, then deploys and starts an ordinary successful mission. It registers
an unpromoted second release and delays a real qualification response while
switching selections to check stale-read gating. Only the response delivery is
delayed; no evaluation/mission results are mocked. The flow also enrolls and registers a camera-profile device and the recorded SmolVLA manifest, checking profile display without running visual inference. CI runs both browser flows.

`node scripts/test-live-pairing.mjs CONNECTION_JSON NEW_OUTPUT_DIRECTORY` targets
an already-running paired API, evaluation job, coordinator, planner and learned
action worker. Its private descriptor uses the same connection fields as the
standard harness plus `planner_backend_kind: "controlled-text-planner"`. It checks
the nested manifest display, explicit registration capability, one paired seed-0
evaluation, accepted planner evidence, real action count, promotion and mobile
layout. It starts/stops only its own web server and browser. This check's controlled
planner does not qualify a Jetson/cloud planning model; declared placement is not
host attestation. Keep the descriptor, credentials and process logs private.

Episode camera playback and the shared device/application navigation are documented in [the workspace guide](../../../../docs/v1/unified-workspace.md).
