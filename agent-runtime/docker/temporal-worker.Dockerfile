FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv
COPY core /srv/core
COPY agent-runtime /srv/agent-runtime
RUN pip install /srv/core /srv/agent-runtime \
    && useradd --create-home --uid 10001 convoy

USER 10001:10001
CMD ["python", "-m", "convoy_runtime.worker"]
