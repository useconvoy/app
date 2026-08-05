# LiteLLM proxy image, built fully offline: every build input (Python wheels,
# prisma engine binaries, the vendored prisma CLI) is prefetched onto the host
# by prefetch.sh before `docker compose build`. The build itself makes no
# network requests, matching the other local containers.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PRISMA_BINARY_CACHE_DIR=/opt/prisma-cache \
    PRISMA_HIDE_UPDATE_MESSAGE=1 \
    CHECKPOINT_DISABLE=1

# Wheels are bind-mounted (not COPYed) so the ~200 MB of build inputs never
# lands in an image layer. fastapi is pinned below 0.141: litellm 1.95 imports
# fastapi.dependencies.utils.get_flat_dependant, which 0.141 removed.
RUN --mount=type=bind,source=wheels,target=/tmp/wheels \
    pip install --no-index --find-links=/tmp/wheels \
    "litellm[proxy]==1.95.0" "prisma==0.15.0" \
    "nodejs-wheel-binaries==24.16.0" "fastapi==0.140.0"

# The prisma CLI is a Node program; expose the wheel-shipped node binary and a
# working npm shim (the wheel's own bin/npm stub cannot resolve npm-cli.js).
RUN NW=$(python -c "import nodejs_wheel, os; print(os.path.dirname(nodejs_wheel.__file__))") && \
    ln -s "$NW/bin/node" /usr/local/bin/node && \
    printf '#!/bin/sh\nexec node %s/lib/node_modules/npm/bin/npm-cli.js "$@"\n' "$NW" \
      > /usr/local/bin/npm && \
    chmod +x /usr/local/bin/npm

# Engine binaries + vendored CLI, prefetched by prefetch.sh on the host. The
# explicit engine paths below are honored by both the JS CLI and the python
# client, so neither ever attempts an engine download.
COPY prisma-cache /opt/prisma-cache
ENV PRISMA_QUERY_ENGINE_BINARY=/opt/prisma-cache/engines/query-engine \
    PRISMA_QUERY_ENGINE_LIBRARY=/opt/prisma-cache/node_modules/@prisma/engines/libquery_engine-debian-openssl-3.0.x.so.node \
    PRISMA_SCHEMA_ENGINE_BINARY=/opt/prisma-cache/node_modules/@prisma/engines/schema-engine-debian-openssl-3.0.x

# Generate the prisma python client at build time (offline: engines above).
# At runtime litellm only runs `prisma db push --skip-generate` against the DB.
RUN SCHEMA=$(python -c "import litellm, pathlib; print(pathlib.Path(litellm.__file__).parent / 'proxy' / 'schema.prisma')") && \
    prisma generate --schema "$SCHEMA"

COPY config.yaml /srv/litellm-config.yaml

EXPOSE 4000
# Long start period: first boot runs `prisma db push` to create the key tables.
HEALTHCHECK --interval=3s --timeout=3s --start-period=90s --retries=40 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4000/health/liveliness')"

CMD ["litellm", "--config", "/srv/litellm-config.yaml", "--host", "0.0.0.0", "--port", "4000", "--use_prisma_db_push"]
