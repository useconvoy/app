FROM node:22-alpine

WORKDIR /opt/convoy/worlds
COPY worlds/runtime ./runtime
COPY worlds/catalog ./catalog

ENV PORT=8788
ENV CONVOY_WORLDS_ROOT=/opt/convoy/worlds
ENV CONVOY_WORLD_STATE_ROOT=/data

RUN addgroup -S convoy && adduser -S -G convoy -u 10001 convoy \
    && mkdir -p /data \
    && chown -R convoy:convoy /opt/convoy/worlds /data

USER convoy
EXPOSE 8788
CMD ["node", "runtime/server.mjs"]
