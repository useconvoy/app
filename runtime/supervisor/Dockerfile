FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git jq openssh-client python3 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 convoy \
    && useradd --uid 10001 --gid convoy --create-home --shell /bin/bash convoy \
    && mkdir -p /opt/convoy/runtime/supervisor /workspace /convoy/output /convoy/scenario \
    && chown -R convoy:convoy /opt/convoy /workspace /convoy

WORKDIR /workspace
COPY contracts /opt/convoy/contracts
COPY runtime/supervisor/supervisor.mjs /opt/convoy/runtime/supervisor/supervisor.mjs
COPY runtime/supervisor/mock-agent.mjs /opt/convoy/mock-agent.mjs

USER convoy
ENTRYPOINT ["node", "/opt/convoy/runtime/supervisor/supervisor.mjs"]
