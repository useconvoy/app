# Convoy landing page

The public site for Convoy, deployment infrastructure for physical AI. One
route, static-first: a hero, the problem, the Package / Qualify / Release
workflow, the execution path, the design-partnership invitation, six FAQ
boundaries, and a contact form. Copy is verbatim from the approved research
and design brief (Parts XI–XVII) and describes a product in development.

## Stack

Next.js 16 (App Router, Turbopack), React 19, TypeScript, Tailwind v4 over a
single semantic token file. IBM Plex Sans and Mono are self-hosted under the
SIL Open Font License. No component library, no animation framework, no
analytics.

```
src/app/                 layout, page, 404, robots, sitemap, link-preview image
src/components/          one component per page section plus header, footer, envelope, contact
src/content/homepage.ts  every public sentence on the page
src/content/claims.ts    the claims ledger: text, stage, evidence, approval
src/content/support.ts   the supported-configuration matrix (empty until tested)
infra/deploy/            the release script that runs on the instance
src/styles/tokens.css    the only file that may contain a hex color
src/styles/globals.css   Tailwind theme bridge, type scale, controls
tests/e2e/               Playwright: accessibility, keyboard, form, screenshots
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
pnpm run test:e2e         # against the production build; writes tests/screenshots/
pnpm run verify           # all of the above, in order
```

The e2e suite is the desktop-and-mobile release gate: axe at 1440, 1280,
768, 390 and 320 with every disclosure open; no sideways scroll at any
width; 44px control targets; the skip link; the mobile menu (aria-expanded,
Escape, focus return); every on-page anchor; the diagrams reflowing to an
ordered vertical structure on mobile with the controller and safety
boundary intact; 200% zoom reflow and the contact address wrapping at 320;
reduced-motion rendering; the mailto link's address, subject and template;
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
