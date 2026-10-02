# Convoy website and workspace

This Next.js app serves the [Convoy landing page](https://deployconvoy.com/) and the
authenticated workspace at `/app`: Projects and Configurations. The landing page
introduces the robot deployment workflow in development; **Open demo** leads to the
workspace sign-in. Demo credentials are provided privately by the operator and must
never be embedded in this repository.

The earlier Jetson portal and console pages (Applications, Device, Chat, Usage and
Traces) have been removed; their URLs redirect to `/app/configurations`. The workspace
device's live telemetry and inference traces appear on its Configurations robot page.
The [production portal record](../docs/production-portal.md) keeps the historical
deployment and physical verification.

## Stack and layout

Next.js 16 App Router, React 19, TypeScript, and Tailwind v4 over a single semantic
token file. IBM Plex Sans and Mono are self-hosted under the SIL Open Font
License. There is no component library, animation framework, or analytics.

```text
src/app/                 landing, workspace (/app), metadata, 404, robots, sitemap, manifest
src/app/api/platform/    session-forwarding proxy to the management API; device chat (platform-chat-v1)
src/app/api/portal/      configured-device snapshot, session probe, device chat API
src/components/          landing sections, header, footer, diagrams, contact
src/components/configurations/  workspace frame, sign-in, Configurations pages, replay player
src/components/projects/ Projects pages; policy-tools/ for existing-runner robots
src/content/homepage.ts  landing copy
src/content/claims.ts    claims ledger: stage, scope, evidence, approval
src/content/support.ts   tested physical text-inference configuration
src/lib/                 Configurations, platform proxy, Projects and device API modules
src/styles/tokens.css    the only source file that may contain a hex color
src/styles/globals.css   Tailwind bridge, shared typography and controls
src/styles/              configurations.css (workspace), console.css (sign-in, policy tools)
src/assets/brand/        brand source assets
public/                  versioned icons, share cards, hero artwork; sim/ robot previews
infra/deploy/            existing Lightsail web-release script
tests/e2e/               Playwright UI, accessibility, keyboard, responsive checks
tests/{configurations,platform,portal}/  unit and server API contract tests
```

`design/reference/` is preserved visual reference material. The current source
of implemented color roles is `src/styles/tokens.css`; the workspace shares those
roles, fonts, small radii, and focus styling. Conceptual landing diagrams remain
labelled as design intent; the workspace's measurements come from the backend.

## Develop

```bash
pnpm install --frozen-lockfile
cp .env.example .env.local
pnpm run dev
```

The landing page runs without backend configuration. The workspace additionally
requires the server-only runtime variables documented in
[src/lib/platform/README.md](src/lib/platform/README.md) and
[src/lib/portal/README.md](src/lib/portal/README.md), and an internal control-plane
connection. Public deployments require HTTPS for the secure session cookie. Never use
`NEXT_PUBLIC_` for a password, session secret, or upstream operator token.

## Verify

```bash
pnpm run typecheck
pnpm run lint
pnpm run check:tokens
bash scripts/test-portal.sh
pnpm run test:platform
pnpm run test:configurations
pnpm run build
pnpm exec playwright install chromium webkit
pnpm run test:e2e
```

`pnpm run verify` runs the same checks in order. Contract fixtures exist only in test files; they
do not prove physical hardware or public deployment acceptance. The workspace suites
mock every API with contract fixtures and include axe, overflow and redirect checks.
The live browser checks against a real API, worker and simulator are described in
[src/lib/platform/README.md](src/lib/platform/README.md).

The landing suite covers Chromium/WebKit, mobile and landscape reflow, keyboard
navigation, anchor clearance, native disclosures, contact links, enlarged text,
static diagrams, metadata, redirects, and the illustrated hero's optional motion.
Its hero test additionally checks OS reduced motion, pause/resume, offscreen
suspension, hidden-page behavior, and touch scrolling. Set
`PLAYWRIGHT_CHROMIUM_PATH` only when using an externally supplied Chromium binary.

## Data and request behavior

The browser reads the management API only through `/api/platform/`, which forwards the
caller's own session to an allowlisted set of routes, and the configured device through
`/api/portal/snapshot`. Neither exposes an arbitrary proxy, raw log browser or
browser-selected endpoint. Telemetry samples and inference traces carry their own
provenance; missing values remain **Not reported**, and browser time is never substituted
for a device measurement timestamp.

## Brand assets and metadata

Brand sources live in `src/assets/brand/`. `scripts/build-brand.mjs` renders the
favicon, versioned PNG/SVG icons, and 1200 × 630 social card with Chromium. The
paths are defined by `BRAND_VERSION` and `SHARE_VERSION` in
`src/content/homepage.ts`. Bump the relevant version when artwork changes and
retain earlier published assets for cached links.

```bash
node scripts/build-brand.mjs [review-dir]
node scripts/measure-page.mjs http://127.0.0.1:3100
```

The landing page's canonical URL is `https://deployconvoy.com/`. Its metadata,
structured data, robots, and sitemap agree on that URL. The workspace (`/app`) is
marked `noindex, nofollow`. Authentication is enforced by the server APIs; robots
metadata is not access control. The `www` host redirects to the apex.

## Contact

The landing contact link opens the visitor's email app with the address, subject,
and deployment-inquiry template. That contact interaction does not submit form
data to Convoy. Workspace sign-in is a separate server request.
`CONTACT_EMAIL` in `src/content/homepage.ts` is the default address;
`NEXT_PUBLIC_CONTACT_EMAIL` overrides it at build time.

## Deployment on the existing Lightsail host

The website uses the existing `convoy-console-demo` Lightsail instance, its static
IP, Caddy TLS termination, and existing DNS. The workspace does not require a new AWS
resource. Its server APIs connect to the internal control plane, and the Jetson agent
connects with direct outbound HTTPS.

The existing `.github/workflows/deploy-website.yml` builds standalone Next.js
output, ships it to the instance over SSH, and invokes
`infra/deploy/remote-release.sh`. Runtime configuration and the physical
agent's direct AWS transport are operator deployment steps; a web build alone
does not create that connection. Review the current release workflow and server
configuration when deploying rather than assuming the old landing-only health
check proves device access.

A web rollback restores the selected web release. It does not prove that the
physical agent, control-plane database, or backend configuration has been rolled
back with it. Preserve the existing backup and identity records and verify the
public session and the physical snapshot after a change.

Historical marketing paths such as `/platform`, `/demo`, and `/early-access`
continue to redirect to `/`. The removed console paths `/app/applications`,
`/app/device`, `/portal` and `/console` redirect permanently to `/app/configurations`.
`/app` opens the authenticated workspace at `/app/projects`; `/sign-in` is not recreated.
