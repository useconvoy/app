# syntax=docker/dockerfile:1.7
# Build with --platform linux/arm64 and the curated planner_assets named context.
# No model downloads occur during build or startup.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.8.24@sha256:1d31be550ff927957472b2a491dc3de1ea9b5c2d319a9cea5b6a48021e2990a6
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS packages
COPY --from=uv /uv /bin/uv
WORKDIR /app/integrations/planner
COPY control-plane/contracts /app/control-plane/contracts
COPY control-plane/agent /app/control-plane/agent
COPY integrations/planner/pyproject.toml integrations/planner/uv.lock ./
COPY integrations/planner/src ./src
RUN uv sync --frozen --no-dev --extra owned --no-editable

FROM ${PYTHON_IMAGE}
ENV PATH="/app/integrations/planner/.venv/bin:${PATH}"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN groupadd --gid 10001 convoy && useradd --uid 10001 --gid convoy --no-create-home convoy \
    && mkdir -p /run/convoy /opt/convoy/assets \
    && chown convoy:convoy /run/convoy && chmod 700 /run/convoy
COPY --from=packages /app/integrations/planner/.venv /app/integrations/planner/.venv
COPY infra/runtime/execution_keys.py infra/planner/entrypoint.py /app/runtime/
COPY --from=planner_assets --chmod=0444 /assets.json /model.gguf /runtime.tar.gz /opt/convoy/assets/
RUN python -c "import importlib.util, json, platform, tempfile; from pathlib import Path; from convoy_planner.local_assets import prepare_text_assets; assert platform.system() == 'Linux' and platform.machine() == 'aarch64'; assert all(importlib.util.find_spec(name) is None for name in ('torch', 'mujoco', 'lerobot', 'convoy_server')); assets=json.loads(Path('/opt/convoy/assets/assets.json').read_text()); temporary=tempfile.TemporaryDirectory(); prepare_text_assets(assets, Path(temporary.name)); temporary.cleanup()"
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --start-period=130s --retries=2 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ready', timeout=4)"]
ENTRYPOINT ["python", "/app/runtime/entrypoint.py"]
CMD ["serve", "--assets", "/opt/convoy/assets/assets.json", "--output", "/run/convoy/state", "--manifest", "/run/convoy/release.json", "--threads", "2", "--threads-batch", "2", "--host", "0.0.0.0", "--port", "8080"]
