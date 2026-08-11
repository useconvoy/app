FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /srv
COPY core /srv/core
COPY agent-runtime /srv/agent-runtime
RUN pip install /srv/core /srv/agent-runtime \
    && useradd --create-home --uid 10001 convoy

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=6 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % __import__('os').environ.get('PORT', '8000'))"
CMD ["sh", "-c", "exec uvicorn convoy_runtime.control_plane.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
