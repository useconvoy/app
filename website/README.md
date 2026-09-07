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
src/app/actions/         the contact server action
src/components/          one component per page section plus header, footer, envelope
src/content/homepage.ts  every public sentence on the page
src/content/claims.ts    the claims ledger: text, stage, evidence, approval
src/content/support.ts   the supported-configuration matrix (empty until tested)
src/lib/contact/         validation and the delivery boundary
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

The e2e suite runs axe on desktop and mobile, checks the skip link, the
mobile menu (aria-expanded, Escape, focus return), every on-page anchor,
reduced-motion rendering, the contact form's validation and preview state,
and captures full-page screenshots at 1440, 768, 390 and 320 CSS pixels
while asserting the page never scrolls sideways. In this session's image the
pinned Chromium is supplied through `PLAYWRIGHT_CHROMIUM_PATH`.

## Contact form: what is still needed

The form is complete but runs in a **preview state**: it validates, keeps
what was typed, and tells the person plainly that sending is not available
yet. It never shows the success state and never stores a submission. To turn
it on:

1. Choose the receiving inbox and a sending provider.
2. Implement that provider in `src/lib/contact/delivery.ts`. The function
   must confirm the destination accepted the message before returning
   `sent`; anything less is `failed`, and the form says so.
3. Set `CONTACT_DELIVERY=<provider id>` and `CONTACT_TO_EMAIL=<inbox>` in the
   runtime environment, then rebuild.
4. Before enabling collection: rate limiting at the edge, a privacy notice,
   and a retention and access policy. The honeypot field is the only spam
   control today.

## Deployment note (Lightsail)

The previous console ran on one Lightsail instance under docker compose
behind Caddy, which terminates TLS with Let's Encrypt and proxies to the
`web` container on port 3000, with Route 53 A records for the apex and `www`
pointing at the instance's static IP. This image fits that box unchanged:

```bash
docker build -t convoy-website .
docker run --rm -p 3000:3000 convoy-website
```

Nothing in this package deploys itself. The old `deploy-console` workflow
assumed the console image (migrations, a catalog seed, an environments
service); it is not carried forward, and a release of this site needs a
simpler compose service: this image as `web`, Caddy in front, no database.
DNS is untouched by this change.

**Historical paths.** The previous site served `/platform`, `/solutions`,
`/security`, `/company`, `/writing`, `/changelog`, `/demo`, `/terms`,
`/privacy`, sign-in and `/app`. They return an ordinary 404 now. Whether to
redirect any of them to `/` is a deployment decision; `next.config.ts`
`redirects()` or a Caddy `redir` block are both fine places for it.
