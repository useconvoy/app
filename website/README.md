# Convoy landing page

The public site for Convoy, deployment infrastructure for physical AI. One
route, static-first: an illustrated humanoid hero, the problem and release diagram, the Package / Qualify / Release
workflow, the execution path, the design-partnership invitation, six FAQ
boundaries, and an email contact section. Copy is verbatim from the approved research
and design brief (Parts XI–XVII) and describes a product in development.

## Stack

Next.js 16 (App Router, Turbopack), React 19, TypeScript, Tailwind v4 over a
single semantic token file. IBM Plex Sans and Mono are self-hosted under the
SIL Open Font License. No component library, no animation framework, no
analytics.

```
src/app/                 layout (metadata, JSON-LD), page, 404, robots, sitemap, manifest
src/assets/brand/        the mark (SVG) and the share card (HTML); scripts/build-brand.mjs renders public/
public/                  favicon.ico, icons/, share/: versioned static brand assets
src/components/          one component per page section plus header, footer, envelope, contact
src/content/homepage.ts  every public sentence on the page
src/content/claims.ts    the claims ledger: text, stage, evidence, approval
src/content/support.ts   the supported-configuration matrix (empty until tested)
infra/deploy/            the release script that runs on the instance
src/styles/tokens.css    the only file that may contain a hex color
src/styles/globals.css   Tailwind theme bridge, type scale, controls
tests/e2e/               Playwright: accessibility, keyboard, contact, screenshots
```

## Develop

```bash
pnpm install
cp .env.example .env.local
pnpm run dev
```

## Verify

```bash
pnpm run typecheck
pnpm run lint
pnpm run check:tokens     # hex literals only in tokens.css
pnpm run build
pnpm exec playwright install chromium webkit  # first-time browser setup
pnpm run test:e2e         # against the production build; writes tests/screenshots/
node scripts/measure-page.mjs http://127.0.0.1:3100   # section heights, visible words, hero CTA position
pnpm run verify           # all of the above, in order
node scripts/build-brand.mjs [review-dir]   # re-render favicon.ico, icons/ and the share card from src/assets/brand/
```

`tests/e2e/hero-motion.spec.ts` checks the illustrated hero asset and its
36px optical focus marker, stable content, responsive lamp placement, soft
brightness changes, pause/resume (including blocked storage), OS reduced
motion, offscreen and hidden-page suspension, and native touch scrolling.
The WebP illustration is preloaded with explicit dimensions. The lamp uses
an image-registered CSS glow; pointer movement updates two custom properties
without creating particles or running a render loop.

The page uses warm paper and clay actions, forest workflow/contact bands,
a teal execution board, and warm partnership paper. Semantic colors live
in `tokens.css`. Both architecture diagrams remain static and accessible;
product copy and the controller/safety boundary are unchanged.

The landing-page suite runs in Chromium and WebKit. Mobile coverage includes
320–430px phones and landscape, single-row navigation, 44px tap targets,
content-driven artwork placement, section-anchor clearance, and enlarged
diagram labels that must not overlap their nodes. The desktop navigation
appears at 1200px; smaller screens retain the compact menu. The hero motion
suite additionally checks lamp placement through orientation changes. iPhone
and Android profiles also exercise the hero, disclosures, contact block,
and mobile navigation using touch input.

`tests/e2e/metadata.spec.ts` checks the crawler-facing surface on the raw
HTML and the static endpoints: title, description, canonical, robots
directives, Open Graph and Twitter tags with an absolute 1200 × 630 PNG,
every declared icon served at its stated size and type (including the
16/32/48 ICO), the browser-mode manifest, valid WebSite and Organization
JSON-LD limited to facts the page states, robots.txt and sitemap agreeing
with the canonical URL, and the www host answered with a 308 to the apex.

## Brand assets and metadata

The mark and the link-preview card were finalized in Claude Design and live
as sources in `src/assets/brand/`. `scripts/build-brand.mjs` renders them
with the pinned Chromium into `public/`: `favicon.ico` (16, 32 and 48 px
PNGs in one container, at the root where browsers and crawlers look first),
an SVG, and 96, 180, 192 and 512 px PNGs under `icons/`, plus the 1200 × 630
card under `share/`. Icon paths carry `BRAND_VERSION` and the card carries
`SHARE_VERSION`, both from `src/content/homepage.ts`; bump the card's
version when its artwork changes so link-preview caches fetch the new card
(new posts only: already-cached posts keep the image they stored), and keep
the previous PNG in `public/share/`. Icon URLs and `favicon.ico` stay
stable. The renderer checks that every face loaded, that the marked text
stays inside the 80 px column with the art clear of it, that the headline
sets on two lines, and that text contrast is at least 4.5:1. The icons are not padded for maskable use and are not
declared as such.

The canonical URL is `https://deployconvoy.com/` (apex, https, trailing
slash) and the same string appears in the canonical link, `og:url`, the
sitemap and the JSON-LD ids. `next.config.ts` sets `trailingSlash` so the
framework emits that form, with `skipTrailingSlashRedirect` so no
normalizing hop is added; the legacy paths still redirect straight to `/`.
The `www` host is answered by the app with a 308 to the apex (Caddy passes
the original Host header); DNS and TLS are unchanged.

The e2e suite is the desktop-and-mobile release gate: axe at 1440, 1280,
768, 390 and 320 with every disclosure open; no sideways scroll at any
width; 44px control targets; the skip link; the mobile menu (aria-expanded,
Escape, focus return); every on-page anchor; the diagrams reflowing to an
ordered vertical structure on mobile with the controller and safety
boundary intact; 200% zoom reflow and the contact address wrapping at 320;
both diagrams static and complete at first paint in normal and
reduced motion at 1440, 393 and 430 with no control, live region or
animation remnants; the mailto link's address, subject and template;
and the legacy redirects. It writes full-page screenshots to
`tests/screenshots/`. In this session's image the
pinned Chromium is supplied through `PLAYWRIGHT_CHROMIUM_PATH`.

## Contact

Inquiries go by email. The contact section carries a mailto link that opens
the visitor's own mail app with the subject "Convoy deployment inquiry" and a
three-line template (model, robot configuration, deployment challenge), plus
the address as selectable text. Nothing is collected or stored on the site.

The address is one constant, `CONTACT_EMAIL` in `src/content/homepage.ts`,
and `NEXT_PUBLIC_CONTACT_EMAIL` overrides it at build time. Changing it is a
one-line edit and a redeploy.

## Deployment (Lightsail)

The site runs on the existing Lightsail instance `convoy-console-demo`
(us-west-2a, static IP, Caddy terminating TLS with Let's Encrypt, Route 53 A
records for the apex and `www`). Nothing about that box, its DNS, or its
certificate changes for a release.

`.github/workflows/deploy-website.yml` runs on every push to `main` that
touches `website/` (and by hand from the Actions tab). It builds the
standalone Next.js output on a standard Linux runner, copies one tarball to
the instance over SSH, and runs `infra/deploy/remote-release.sh`, which:

1. copies `compose.yaml`, `compose.override.yaml`, `.env` and `Caddyfile` to
   `/opt/convoy/rollback/<timestamp>/` together with the image the `web`
   service was running;
2. unpacks the release into `/opt/convoy/releases/<sha>/`;
3. rewrites only the `web` service in `compose.yaml` to run that directory
   with the stock `node:22-alpine` image (`node server.js`, read-only bind
   mount, unprivileged user);
4. recreates `web`, checks it through the compose network the way Caddy
   reaches it, and restores the backup if the check fails.

The previous console's image stays in ECR and in the local Docker cache, and
its other services (postgres, notifier, the environments override) are left
exactly as they were. **Rollback:** run the workflow with `rollback` checked
(and `rollback_to` naming a backup directory, or empty for the newest), or on
the instance `sudo bash /tmp/remote-release.sh rollback [backup]`. The
pre-launch state of the previous console is `rollback/20260907T015223Z`.

No registry push, snapshot, or new AWS resource is involved. The GitHub
Actions minutes come from the organization's included allowance.

**Historical paths.** `/platform`, `/solutions`, `/security`, `/company`,
`/writing`, `/changelog`, `/demo`, `/early-access`, `/terms` and `/privacy`
redirect permanently to `/` (see `next.config.ts`). The old authenticated
console routes (`/app`, `/sign-in`, `/api/...`) return an ordinary 404 and
are not recreated.
