# Convoy website and Jetson portal

This Next.js app serves the [Convoy landing page](https://deployconvoy.com/) and
[authenticated Jetson demo](https://deployconvoy.com/portal). The landing page
introduces the robot deployment workflow in development. **Open demo** leads to
Device, Chat, Usage, and Traces for one configured physical Jetson.

The demo uses received device data and real model responses. Its verified
text-inference configuration and limits are recorded in the
[production portal architecture and walkthrough](../docs/production-portal.md).
That document distinguishes preserved physical evidence, software contract
validation, and public deployment acceptance. Demo credentials are provided
privately by the operator and must never be embedded in this repository.

## Stack and layout

Next.js 16 App Router, React 19, TypeScript, and Tailwind v4 over a single semantic
token file. IBM Plex Sans and Mono are self-hosted under the SIL Open Font
License. There is no component library, animation framework, or analytics.

```text
src/app/                 landing, portal, metadata, 404, robots, sitemap, manifest
src/app/api/portal/      session, physical-device snapshot, bounded text Chat
src/components/portal/  portal shell, login, device/usage/trace views, Chat, charts
src/components/         landing sections, header, footer, diagrams, contact
src/content/homepage.ts landing copy
src/content/claims.ts   claims ledger: stage, scope, evidence, approval
src/content/support.ts  tested physical text-inference configuration
src/lib/portal/         server-only authentication, upstream boundary, curated types
src/styles/tokens.css   the only source file that may contain a hex color
src/styles/globals.css  Tailwind bridge, shared typography and controls
src/styles/portal.css   responsive portal layout using the same semantic tokens
src/assets/brand/       brand source assets
public/                 versioned icons, share cards, hero artwork
infra/deploy/           existing Lightsail web-release script
tests/e2e/              Playwright UI, accessibility, keyboard, responsive checks
tests/portal/           isolated server API contract/security tests
```

`design/reference/` is preserved visual reference material. The current source
of implemented color roles is `src/styles/tokens.css`; the portal shares those
roles, fonts, small radii, and focus styling. Conceptual landing diagrams remain
labelled as design intent; the portal's measurements come from the backend.

## Develop

```bash
pnpm install --frozen-lockfile
cp .env.example .env.local
pnpm run dev
```

The landing page runs without backend configuration. The portal additionally
requires the server-only runtime variables documented in
[src/lib/portal/README.md](src/lib/portal/README.md), a configured physical device,
and an internal control-plane connection. Public deployments require HTTPS for
the secure session cookie. Never use `NEXT_PUBLIC_` for a password, session secret,
or upstream operator token.

## Verify

```bash
pnpm run typecheck
pnpm run lint
pnpm run check:tokens
bash scripts/test-portal.sh
pnpm run build
pnpm exec playwright install chromium webkit
pnpm run test:e2e
```

For the portal alone after a production build:

```bash
pnpm exec playwright test tests/e2e/portal.spec.ts --project=chromium
```

`pnpm run verify` runs the package's typecheck, lint, token, build, and browser
checks. Run `bash scripts/test-portal.sh` as well for the dedicated server API
checks. Contract fixtures exist only in test files; they do not prove physical
hardware or public deployment acceptance.

The portal suite covers login/logout, queued/running/completed requests, recovery
of an uncertain submission using the same UUID, conversation context, expiry,
offline blocking, input limits, exact data tables, and distinct browser/device
clocks. Axe and overflow checks cover 320, 390, 768, and 1440 pixels; enlarged-text
checks exercise 200% text at a phone viewport. Screenshots are written to
`tests/screenshots/portal-*.png`.

The landing suite covers Chromium/WebKit, mobile and landscape reflow, keyboard
navigation, anchor clearance, native disclosures, contact links, enlarged text,
static diagrams, metadata, redirects, and the illustrated hero's optional motion.
Its hero test additionally checks OS reduced motion, pause/resume, offscreen
suspension, hidden-page behavior, and touch scrolling. Set
`PLAYWRIGHT_CHROMIUM_PATH` only when using an externally supplied Chromium binary.

## Data and request behavior

The browser uses only the session, snapshot, and Chat endpoints under
`/api/portal/`. The server holds a dedicated operator token, validates the single
physical device, and discards fleet aggregates and simulator data before returning
curated fields. It exposes no arbitrary proxy, deployment control, enrollment,
configuration mutation, or raw log browser.

Telemetry samples, received usage, and the bounded inference sample carry their
own provenance. Missing values remain **Not reported**. Browser refresh time is
not substituted for a device measurement timestamp. Chat returns the full reply
when complete; no token stream or device measurement is simulated. The UI keeps
conversation history in memory while the portal stays open in the browser tab.

See [the walkthrough](../docs/production-portal.md#walkthrough) for the interaction
sequence and [data definitions](../docs/production-portal.md#what-the-numbers-mean)
for the 30-day accounting window, delayed records, overlapping clocks, and sample
percentile limitations.

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
structured data, robots, and sitemap agree on that URL. The portal has its own
title and canonical route and is marked `noindex, nofollow`. Authentication is
enforced by the portal API; robots metadata is not access control. The `www` host
redirects to the apex.

## Contact

The landing contact link opens the visitor's email app with the address, subject,
and deployment-inquiry template. That contact interaction does not submit form
data to Convoy. Portal authentication and Chat are separate server requests.
`CONTACT_EMAIL` in `src/content/homepage.ts` is the default address;
`NEXT_PUBLIC_CONTACT_EMAIL` overrides it at build time.

## Deployment on the existing Lightsail host

The website uses the existing `convoy-console-demo` Lightsail instance, its static
IP, Caddy TLS termination, and existing DNS. The portal does not require a new AWS
resource. Its production architecture adds a bounded website API connected to
the internal control plane and direct outbound HTTPS from the Jetson agent.

The existing `.github/workflows/deploy-website.yml` builds standalone Next.js
output, ships it to the instance over SSH, and invokes
`infra/deploy/remote-release.sh`. Runtime portal configuration and the physical
agent's direct AWS transport are operator deployment steps; a web build alone
does not create that connection. Review the current release workflow and server
configuration when deploying rather than assuming the old landing-only health
check proves device access.

A web rollback restores the selected web release. It does not prove that the
physical agent, control-plane database, or backend configuration has been rolled
back with it. Preserve the existing backup and identity records and verify the
public session, physical snapshot, and an actual Chat request after a change.

Historical marketing paths such as `/platform`, `/demo`, and `/early-access`
continue to redirect to `/`. `/portal` and `/api/portal/...` are the new demo
surface. `/app` opens the authenticated workspace at `/app/configurations`;
`/sign-in` is not recreated.
