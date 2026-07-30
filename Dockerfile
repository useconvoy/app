# Convoy Labs — control plane + agent runtime (single container).
# See docs/deployment.md for the hosted topology and environment variables.

# ---- build ----
FROM node:22-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# ---- run ----
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0
# Demo state lives on a mounted volume; wipe the volume to reseed.
ENV CONVOY_DATA_DIR=/data

RUN addgroup -S convoy && adduser -S convoy -G convoy \
  && mkdir -p /data && chown convoy:convoy /data

COPY --from=builder --chown=convoy:convoy /app/.next/standalone ./
COPY --from=builder --chown=convoy:convoy /app/.next/static ./.next/static

USER convoy
EXPOSE 3000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD wget -qO- http://127.0.0.1:3000/api/health || exit 1

CMD ["node", "server.js"]
