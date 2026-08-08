# website

The Convoy console, BFF, and identity/commerce layer: every screen users
see, the org/team/role structure behind them, in-app notifications, feedback
capture, the catalog storefront, and billing. It is not a domain service —
runs, plans, checkpoints, workspaces, and evaluations are owned by other
subsystems and consumed through the control plane's single authenticated
edge.

## Stack

Next.js (App Router) + TypeScript + Tailwind. The Next server layer is the
BFF: server components fetch initial state, route handlers proxy actions and
SSE. Domain types come exclusively from the generated OpenAPI client
(`src/lib/api/schema.d.ts`); the website's own Postgres holds only
identity/org tables, notifications, feedback, catalog, and billing state,
all under row-level security keyed to the active organization.

```
src/styles/tokens.css   design tokens; the only file where hex may appear
src/lexicon.ts          the only door between internal and user-facing vocabulary
src/lib/format.ts       friendly dates, money, ages (facts from the log)
src/lib/db.ts           RLS-scoped database access (org context per transaction)
src/lib/api/            generated control-plane client + auth bridge
src/components/         the component library (route motif, checkpoint cards, ...)
src/app/(marketing)     the marketing site
src/app/(auth)          sign-in, onboarding, org switching, invites
src/app/(portal)/app    the console shell and its surfaces
src/notifier/           the notification worker (events feed -> bell panel)
db/migrations/          website schema + RLS policies
```

How the lexicon's user-facing terms map to backend names across services is tabled in [docs/LEXICON.md](../docs/LEXICON.md).

## Development

```bash
pnpm install
cp .env.example .env.local          # defaults match the local runtime stack
npm run db:migrate                  # apply db/migrations
npm run dev
```

The portal expects the runtime's local stack. With Docker available, run
`make e2e-up` at the repo root, then start the worker and control plane with
`scripts/dev-runtime.sh`. Without Docker, `scripts/local-stack.sh` stands up
the same services natively (Postgres, Temporal dev server, an S3-compatible
store, the stub environment registry).

Sign-in uses WorkOS AuthKit when `WORKOS_API_KEY`/`WORKOS_CLIENT_ID` are
configured; otherwise a clearly-labeled local development provider is
offered behind the same session contract.

## Checks and tests

```bash
npm run verify        # typecheck + lint + lexicon/token checks + unit/component tests
npm run check:lexicon # internal nouns and em dashes never reach rendered output
npm run check:tokens  # hex values exist only in tokens.css
npm run generate:client  # regenerate the typed client from the control plane
npm run test:e2e      # Playwright against the local runtime stack
```

The pyramid: unit tests for the lexicon, formatting, permissions, and
notifier rules; component tests (Vitest + Testing Library + axe) for every
library component and surface state; integration tests against a real
Postgres for RLS isolation and notifier idempotency; Playwright E2E against
the real control plane with no mocked domain. SSE fixtures under
`tests/fixtures/sse/` are recorded from real control-plane runs
(`scripts/record-sse-fixtures.mjs`); regenerate them rather than editing by
hand.
