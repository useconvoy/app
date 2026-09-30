# Device connection API

Device connection is a section of `/app/applications`. It contains the existing
physical-device telemetry, model chat, usage and trace views. `/portal?view=...`
and `/app/device?view=...` redirect there. The application provides the only login
and logout through `/api/platform/auth/login` and `/api/platform/auth/logout`.
The legacy `GET /api/portal/session` remains a read-only session/health adapter;
its independent POST/DELETE login flow has been removed.

The device BFF forwards only the caller's `convoy_session` to the same configured
control plane as the application. It never uses a shared operator token. The API
validates session expiry/revocation and permissions on every request: viewers can
inspect device evidence; operators/admins can send chat. Chat ownership is recorded
against the actual user. Device APIs are installation-wide today, while robot
application APIs enforce project ownership. Do not present the configured Jetson
as automatically bound to the selected application's robot.

Runtime configuration (server-only):

| Variable | Value |
| --- | --- |
| `CONVOY_API_URL` | Same API origin as application management; localhost default |
| `CONVOY_API_INTERNAL_HTTP` | Optional `1` for the exact private `http://control-plane:8080` Compose origin |
| `CONVOY_DEVICE_ID` | Allowlisted physical `dev_...` ID |
| `CONVOY_CONSOLE_ORIGIN` | Exact external origin; falls back to `PORTAL_PUBLIC_ORIGIN` |

The old `CONVOY_UPSTREAM_URL`, `CONVOY_UPSTREAM_TOKEN`, `PORTAL_DEMO_EMAIL`,
`PORTAL_DEMO_PASSWORD_HASH` and `PORTAL_SESSION_SECRET` are no longer used.
Deployment does not automatically delete these runtime values, so an older release
can still be restored. Demo-only credentials do not become management accounts;
users need an existing Convoy account with the intended role.

The shared HttpOnly cookie is scoped to `/api`, SameSite=Strict, and Secure on
HTTPS. Successful login or a validated existing management session migrates the
old `/api/platform` cookie to the shared scope and clears the old demo cookie.
Migration does not renew the session's control-plane expiry. Sign-out revokes the
same session for application and device requests. Duplicate session cookies are
rejected; signing in clears the old scope. Device POST requests require both an
exact Origin and `X-Convoy-Client: web`.

Device data paths validate the configured physical device and use a fixed path
allowlist. There is no arbitrary URL/device proxy, deployment endpoint, raw log
or arbitrary-attribute access. Fleet usage totals are discarded. Missing metrics
stay null. Inference samples may contain other gateway requests on this device;
`latency_ms` is gateway/model time, separate from browser roundtrip time.

The BFF derives a request UUID using the opaque session credential as an HMAC key
and the browser's UUID as input. Another session gets a different upstream UUID.
Only the browser UUID is returned. An uncertain POST is reconciled by polling that
same UUID, never automatically resubmitted.

Limits remain per process: 20 chat submissions/minute globally, 6/session/minute;
30 snapshots/session/minute; 180 chat polls/session/minute. The map is capped at
4096 entries. Authentication throttling is owned by the control plane. Scale-out
requires a shared limiter for fleet-wide caps. Request bodies and upstream
responses are bounded, each upstream fetch has an eight-second deadline, redirects
are rejected, and mutations are not retried. Errors are curated and all responses
are private/no-store. Backend 401/403 stay authentication/permission failures.

`Test connection` refreshes control-plane evidence and shows whether the latest
nonce-bound live report indicates recent device contact. It does not send robot
commands or prove an inference roundtrip. Use Chat for a bounded model test;
its measured response and trace are available in the same section.

Run `bash scripts/test-portal.sh` for focused authentication, permissions,
request-isolation, curation and recovery checks. Existing browser regressions
exercise all four device views inside the shared workspace.
