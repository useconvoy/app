FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    CONVOY_CHROMIUM_BINARY=/usr/bin/chromium

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates chromium tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 sandbox

USER 1000:1000
ENTRYPOINT ["/usr/bin/tini", "--"]
# EcsSandboxProvider replaces this command with its integrity-pinned bootstrap.
CMD ["python3", "-c", "import time; time.sleep(3600)"]
