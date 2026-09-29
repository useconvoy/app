# Convoy landing page. Standalone Next.js output served by `node server.js`
# on port 3000; the same image works under docker compose behind Caddy on
# the Lightsail box (see README.md) or anywhere else a container runs.
ARG NODE_IMAGE=node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402

FROM ${NODE_IMAGE} AS base
ENV PNPM_HOME=/pnpm
ENV PATH="${PNPM_HOME}:${PATH}"
ENV NEXT_TELEMETRY_DISABLED=1
RUN corepack enable
WORKDIR /app

FROM base AS deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
RUN pnpm install --frozen-lockfile

FROM base AS builder
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN pnpm run build

FROM ${NODE_IMAGE} AS runner
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
RUN addgroup -S -g 1001 convoy && adduser -S -u 1001 -G convoy convoy
COPY --from=builder --chown=convoy:convoy /app/.next/standalone ./
COPY --from=builder --chown=convoy:convoy /app/.next/static ./.next/static
COPY --from=builder --chown=convoy:convoy /app/public ./public
USER convoy
EXPOSE 3000
CMD ["node", "server.js"]
