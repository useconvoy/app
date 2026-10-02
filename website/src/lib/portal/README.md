# Device connection API

`/api/portal/` is the server-side API for the workspace's one configured physical
device. It has no pages of its own: the earlier device pages (Device, Chat, Usage and
Traces) were removed with the old console, and `/portal` and `/app/device` now redirect
to `/app/configurations`. Its current callers are:

- `GET /api/portal/snapshot`: the Configurations live-device poller
  (`src/lib/configurations/live.ts`), for robots bound to the configured device.
- `POST /api/portal/chat`, `GET /api/portal/chat/{requestId}`: no caller. The simulation's
  real-device planner moved to the public device chat contract (`platform-chat-v1`,
  `/api/platform/chat/devices` and `/api/platform/devices/{id}/chat`, in
  `src/lib/platform/chat.ts`), which keeps these routes' limits and checks for any physical
  device. No browser page sends chat.
- `GET /api/portal/session`: a read-only session/health adapter, used by the deployment
  health check. Login and logout use only `/api/platform/auth/login` and `/logout`.

The API forwards only the caller's `convoy_session` to the same configured control plane
as the workspace. It never uses a shared operator token. The control plane validates
session expiry/revocation and permissions on every request: viewers can read device
evidence; operators/admins can send chat. Device APIs are installation-wide today, while
robot application APIs enforce project ownership. Do not present the configured Jetson as
automatically bound to a project's robot.

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
can still be restored.

The shared HttpOnly session cookie is scoped to `/api`, SameSite=Strict, and Secure on
HTTPS. Sign-out revokes the same session for workspace and device requests. Duplicate
session cookies are rejected. Device POST requests require both an exact Origin and
`X-Convoy-Client: web`.

Device data paths validate the configured physical device and use a fixed path
allowlist. There is no arbitrary URL/device proxy, deployment endpoint, raw log or
arbitrary-attribute access. Fleet usage totals are discarded and missing metrics stay
null. Chat request ids are derived per session (an HMAC keyed by the session credential),
so another session cannot read a request; an uncertain POST is reconciled by polling the
same id, never resubmitted automatically. Limits are per process (snapshots: 30 per
session per minute; chat: 6 submissions and 180 polls per session per minute), request
bodies and upstream responses are bounded, each upstream fetch has an eight-second
deadline, redirects are rejected, and errors are curated with private/no-store responses.

Run `bash scripts/test-portal.sh` for the authentication, permission, request-isolation,
curation and recovery checks.
