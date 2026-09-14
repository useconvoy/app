# Portal BFF

The public browser uses only `/api/portal/session`, `/api/portal/snapshot`, and
`/api/portal/chat[/<client UUID>]`. The BFF connects to the control plane with a
server-held operator token and returns selected fields for one configured device.
It has no general proxy, fleet aggregates, raw logs, arbitrary attrs or deployment
actions. Every device data path validates the configured physical device.

Runtime configuration (never `NEXT_PUBLIC_`):

| Variable | Value |
| --- | --- |
| `CONVOY_UPSTREAM_URL` | Exact internal control-plane HTTP(S) origin, no path or credentials |
| `CONVOY_UPSTREAM_TOKEN` | Dedicated operator `cva_...` API token |
| `CONVOY_DEVICE_ID` | Allowlisted `dev_...` ID |
| `PORTAL_DEMO_EMAIL` | Dedicated demo sign-in email |
| `PORTAL_DEMO_PASSWORD_HASH` | `scrypt$<salt hex>$<derived key hex>` |
| `PORTAL_SESSION_SECRET` | Cryptographically random secret, at least 32 characters |
| `PORTAL_PUBLIC_ORIGIN` | Exact external HTTPS origin, e.g. `https://deployconvoy.com`, no trailing slash |

Password hashes use a random 16–32 byte salt and Node's scrypt with `N=16384`,
`r=8`, `p=1`, output length 64 bytes. Store the salt and derived key as lowercase
hex (salt 32–64 characters, key 128 characters). Only the hash enters deployment
configuration; never commit plaintext passwords or hashes used in production.

Sessions are eight-hour HMAC-signed random nonces in a Secure, HttpOnly,
SameSite=Strict, `__Host-` cookie. Changing email, password hash or signing secret
invalidates existing sessions. Sign-out removes the browser cookie; there is no
server-side session database/revocation list. Deployment must provide HTTPS.
POST and DELETE require an Origin header equal to `PORTAL_PUBLIC_ORIGIN`.

The BFF derives each upstream Chat UUID from the signed session nonce plus the
browser's UUID. Another session using the same browser UUID gets a different
upstream UUID. This works across process restarts and multiple processes sharing
configuration. Responses expose only the browser UUID. Do not regenerate after
an uncertain POST: query that same browser UUID to reconcile its result.

Limits are per process: 20 logins/minute globally, 6/email/minute, two simultaneous
password checks; 20 Chat submissions/minute globally and 6/session/minute;
30 snapshots/session/minute; 180 Chat polls/session/minute. Limit state is capped
at 4096 entries. Global limits also bound callers who change identifiers. Scale-out
deployments need a shared limiter or reverse-proxy limits for a fleet-wide cap.
Login JSON is capped at 2 KiB; Chat JSON at 64 KiB with 8 KiB decoded message text.
Body reads and each upstream fetch have eight-second deadlines; upstream payloads
are capped at 2 MiB. Mutations have no automatic retry. All responses are private,
no-store and errors are curated rather than forwarding upstream response bodies.

Usage selects only the configured device's `devices[].metrics`. Fleet totals and
daily sums are discarded. Missing metrics stay null. Inference history is bounded
and includes all gateway inference on that device, potentially evaluations as well
as Chat. `latency_ms` is model/gateway time, not browser end-to-end elapsed time.

Run backend verification with `bash scripts/test-portal.sh`. It uses TypeScript and
Node's test runner with an isolated temporary output directory, no new dependencies.
