FROM node:22-bookworm-slim AS build

RUN corepack enable
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY tsconfig.json ./
COPY src ./src
RUN pnpm build && pnpm prune --prod

FROM node:22-bookworm-slim

ENV NODE_ENV=production
WORKDIR /app
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system convoy \
    && useradd --system --gid convoy --home-dir /app convoy
COPY --from=build --chown=convoy:convoy /app/package.json ./package.json
COPY --from=build --chown=convoy:convoy /app/node_modules ./node_modules
COPY --from=build --chown=convoy:convoy /app/dist ./dist
USER convoy
CMD ["node", "dist/index.js"]
