# Application console

`/console` uses the authenticated lifecycle APIs on the existing Python management server. It does not use the Jetson demo's shared operator token or portal session. The demo at `/portal` is unchanged.

Set server-side `CONVOY_API_URL` to the API origin (default `http://127.0.0.1:8080`). HTTP is permitted only for literal localhost addresses; remote origins require HTTPS. Set `CONVOY_CONSOLE_ORIGIN` to the exact public console origin when a reverse proxy changes the request origin. Neither setting is exposed as a browser-selected endpoint.

Sign in with an existing management account. The proxy forwards only that user's `convoy_session`, scopes it to `/api/platform`, and sets HttpOnly, SameSite=Strict, and Secure on HTTPS. The proxy requires exact Origin and `X-Convoy-Client: web` on every mutation, including login/logout, forwards idempotency keys, bounds request/response bodies, and refuses redirects. No device credential or static operator token is required in the web process.

The initial console supports project creation, simulated device enrollment and robot binding, application creation, immutable manifest registration, deployment requests, separately authorized mission starts, cancellation requests, and recorded episode summaries. A ready deployment is the last acknowledged state, not evidence of current robot health. Control-plane snapshots refresh every three seconds. Lost polling disables deploy/start; it does not claim that execution stopped. The coordinator and inference worker must be running separately.

New APIs enforce project ownership within one installation. The existing device/enrollment APIs remain installation-wide; do not expose this as a fully isolated multi-tenant service. Only the MetaWorld simulator profile is executable in M1.

`pnpm test:platform` checks the proxy's route/scope allowlist, session forwarding, mutation origin checks, body bounds, cookie handling and safe errors. `pnpm typecheck`, `pnpm lint`, `pnpm check:tokens`, and `pnpm build` check the application. End-to-end validation must additionally run the browser against the actual API/coordinator/worker; mocked transport tests do not establish the robot lifecycle.

The live browser check is `node scripts/test-live-console.mjs CONNECTION_JSON NEW_OUTPUT_DIRECTORY`.
Run `pnpm build` first and start the private local stack with
`examples/manipulation/pipeline.py --serve`. The check launches its own loopback web
server, verifies a completed 500-step episode through the actual worker and MuJoCo,
then cancellation, session persistence, mobile layout and logout. CI creates and
cleans up that stack automatically; only screenshots and the credential-free
result are published.
