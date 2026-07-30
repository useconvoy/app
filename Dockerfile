# Convoy Labs — control plane + agent runtime (single container).
# See docs/deployment.md for the hosted topology and environment variables.

# ---- build ----
FROM node:22-alpine AS builder
WORKDIR /app
# Canonical URL for robots.txt/sitemap.xml/OG tags. Build-time, not runtime:
# these routes are statically generated, so the value is baked into the image.
ARG NEXT_PUBLIC_SITE_URL
ENV NEXT_PUBLIC_SITE_URL=${NEXT_PUBLIC_SITE_URL}
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build -- --webpack

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
COPY --from=builder --chown=convoy:convoy /app/public ./public

USER convoy
EXPOSE 3000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD wget -qO- http://127.0.0.1:3000/api/health || exit 1

# App Runner injects its own HOSTNAME value. Force the standalone server to
# listen on all interfaces instead of binding only to the container hostname.
CMD ["sh", "-c", "HOSTNAME=0.0.0.0 exec node server.js"]
