# Convoy console image. One image, three entry points: the Next server
# (`npm run start`), the notification worker (`npm run notifier`, which runs
# TypeScript through tsx), and the schema migrator (`npm run db:migrate`).
# ECS picks between them with `command`, so web, notifier, and the one-off
# migrate task always run the exact same build.
#
# NODE_IMAGE exists so a build behind a registry mirror can point the base
# somewhere reachable without editing this file; the default is the canonical
# tag.
ARG NODE_IMAGE=node:22-alpine

FROM ${NODE_IMAGE} AS base
ENV PNPM_HOME=/pnpm
ENV PATH="${PNPM_HOME}:${PATH}"
ENV NEXT_TELEMETRY_DISABLED=1
RUN corepack enable
WORKDIR /app

# --- Dependencies ----------------------------------------------------------
# The full tree, resolved strictly from the committed lockfile. The runtime
# stage keeps it rather than pruning to production: the notifier's entry point
# is TypeScript executed by tsx, which package.json declares as a dev
# dependency, and a pruned tree would drop it. One tree also means the image
# that passed the build is byte-for-byte the image that serves traffic.
#
# Dependency build scripts stay unrun. pnpm refuses to install silently past
# them, so the refusal is answered here rather than by approving them: the
# three packages that ask (esbuild, msw, an eslint resolver) all ship prebuilt
# platform binaries or browser-only assets, and nothing the web server, the
# notifier, or the migrator does at run time comes out of those scripts.
FROM base AS deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile --config.strictDepBuilds=false

# --- Build -----------------------------------------------------------------
FROM base AS builder
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# `.next/cache` is build scratch that only slows the layer down. The build
# deliberately does not trace a standalone server: the image carries the whole
# dependency tree anyway so the notifier can run TypeScript through tsx, which
# is exactly what a traced copy exists to avoid shipping. One tree serves all
# three entry points.
RUN pnpm run build && rm -rf .next/cache

# --- Runtime ---------------------------------------------------------------
FROM base AS runner
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

RUN addgroup -S -g 1001 convoy && adduser -S -u 1001 -G convoy convoy

COPY --from=deps --chown=convoy:convoy /app/node_modules ./node_modules
COPY --from=builder --chown=convoy:convoy /app/.next ./.next
COPY --chown=convoy:convoy package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY --chown=convoy:convoy next.config.ts tsconfig.json ./
COPY --chown=convoy:convoy src ./src
# The migrator reads the .sql files at run time, so both the runner script and
# the migrations themselves have to be in the image, not just in the repo.
COPY --chown=convoy:convoy db ./db
COPY --chown=convoy:convoy scripts/migrate.mjs ./scripts/migrate.mjs
# The deploy publishes/refreshes the platform catalog entries each release.
COPY --chown=convoy:convoy scripts/seed-doc-steward.mjs ./scripts/seed-doc-steward.mjs
COPY --chown=convoy:convoy infra/db/sync-app-role.mjs ./infra/db/sync-app-role.mjs
# RDS serves a certificate chained to Amazon's own roots, which Node does not
# trust by default. Without this bundle a DSN asking for real verification
# fails to connect, and the only way to reach the database would be to stop
# verifying it.
COPY --chown=convoy:convoy infra/docker/rds-global-bundle.pem ./certs/rds-global-bundle.pem

USER convoy
EXPOSE 3000

CMD ["npm", "run", "start"]
