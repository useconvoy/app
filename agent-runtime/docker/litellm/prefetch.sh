#!/usr/bin/env bash
# Prefetches every build input for the litellm image onto the host:
#   wheels/        - all Python wheels (litellm[proxy], prisma, node, fastapi pin)
#   prisma-cache/  - prisma engine binaries + the vendored prisma CLI
#
# The host's package tooling already trusts the local network path (proxies,
# CA bundles), while `docker build` containers do not — so all downloads happen
# here and the image build itself is fully offline. Idempotent: exits early
# when the inputs are already present. Requires `uv` (the repo toolchain).
set -euo pipefail
cd "$(dirname "$0")"

LITELLM_PIN="litellm[proxy]==1.95.0"
PRISMA_PIN="prisma==0.15.0"
NODE_PIN="nodejs-wheel-binaries==24.16.0"
# litellm 1.95 imports fastapi internals removed in 0.141; pin the last
# compatible release inside litellm's own >=0.136.3,<1.0 requirement.
FASTAPI_PIN="fastapi==0.140.0"

if [ -f wheels/.complete ] && [ -f prisma-cache/.complete ]; then
    echo "litellm build inputs already prefetched"
    exit 0
fi

rm -rf wheels prisma-cache .prefetch-venv
mkdir -p wheels prisma-cache

uv venv --seed -q .prefetch-venv
VENV_PY=".prefetch-venv/bin/python"

"$VENV_PY" -m pip download -q -d wheels \
    "$LITELLM_PIN" "$PRISMA_PIN" "$NODE_PIN" "$FASTAPI_PIN"

# prisma's engine fetcher shells out to npm; give it the wheel-shipped node
# with a working npm shim (the wheel's own bin/npm stub cannot run standalone).
"$VENV_PY" -m pip install -q "$PRISMA_PIN" "$NODE_PIN"
NW="$("$VENV_PY" -c 'import nodejs_wheel, os; print(os.path.dirname(nodejs_wheel.__file__))')"
mkdir -p .prefetch-venv/nodebin
ln -sf "$NW/bin/node" .prefetch-venv/nodebin/node
printf '#!/bin/sh\nexec "%s/bin/node" "%s/lib/node_modules/npm/bin/npm-cli.js" "$@"\n' \
    "$NW" "$NW" > .prefetch-venv/nodebin/npm
chmod +x .prefetch-venv/nodebin/npm

PATH="$PWD/.prefetch-venv/nodebin:$PWD/.prefetch-venv/bin:$PATH" \
    PRISMA_BINARY_CACHE_DIR="$PWD/prisma-cache" \
    "$VENV_PY" -m prisma py fetch

# `prisma py fetch` grabs the CLI package, the query-engine library, and the
# schema engine — but not the standalone query-engine process the generated
# python client talks to. Fetch it directly for the image's platform; the
# engines commit comes from the CLI package just fetched, so it always matches.
ENGINES_COMMIT="$("$VENV_PY" -c "
import json
pkg = json.load(open('prisma-cache/node_modules/@prisma/engines/package.json'))
print(pkg['dependencies']['@prisma/engines-version'].rsplit('.', 1)[-1])
")"
IMAGE_PLATFORM="debian-openssl-3.0.x"  # python:3.12-slim
mkdir -p prisma-cache/engines
curl -fsS -o prisma-cache/engines/query-engine.gz \
    "https://binaries.prisma.sh/all_commits/${ENGINES_COMMIT}/${IMAGE_PLATFORM}/query-engine.gz"
gunzip -f prisma-cache/engines/query-engine.gz
chmod +x prisma-cache/engines/query-engine

touch wheels/.complete prisma-cache/.complete
rm -rf .prefetch-venv
echo "litellm build inputs prefetched: $(ls wheels | wc -l) wheels, $(du -sh prisma-cache | cut -f1) engine cache"
