# syntax=docker/dockerfile:1.7
# One locked CPU reference environment, run as separate worker/device processes.
FROM ghcr.io/astral-sh/uv:0.8.24@sha256:1d31be550ff927957472b2a491dc3de1ea9b5c2d319a9cea5b6a48021e2990a6 AS uv
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
COPY --from=uv /uv /bin/uv
# MuJoCo loads libGL even for a non-rendered, headless rollout.
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libegl1 libglfw3 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 convoy && useradd --uid 10001 --gid convoy --create-home convoy \
    && mkdir -p /robot /certificates/api /certificates/inference /certificates/ca \
    && chown -R convoy:convoy /robot /certificates
ENV PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PATH="/app/integrations/simulation/.venv/bin:${PATH}" \
    SSL_CERT_FILE=/run/ca/ca.crt OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
WORKDIR /app
COPY control-plane/pyproject.toml control-plane/uv.lock ./control-plane/
COPY control-plane/server ./control-plane/server
COPY control-plane/agent ./control-plane/agent
COPY control-plane/contracts ./control-plane/contracts
COPY control-plane/worker ./control-plane/worker
# Native demo runs contain device credentials and connection.json. Copy only
# package inputs; Git's ignore rules do not protect a Docker build context.
COPY integrations/simulation/pyproject.toml integrations/simulation/uv.lock ./integrations/simulation/
COPY integrations/simulation/src ./integrations/simulation/src
RUN uv sync --project integrations/simulation --frozen --no-dev --extra managed --python /usr/local/bin/python3 \
    && python -c 'import json; from convoy_sim.runtimes import reference_manifest; open("/app/release.json", "w").write(json.dumps(reference_manifest()))' \
    && rm -rf /root/.cache
COPY infra/development/containers /app/packaging
USER convoy
# Compose grants /robot only to the device/acceptance processes. Declaring an
# image-wide VOLUME would create unused anonymous volumes for every worker.
CMD ["python", "/app/packaging/simulator.py"]
