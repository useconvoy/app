# Convoy environments service (gateway + console API) for dev/demo stacks.
#
# The build context must be the REPO ROOT, because convoy-core is a path
# dependency of convoy-environments:
#
#     docker build -f environments/Dockerfile .
#
# agent-runtime/compose.yaml's `demo` profile builds it the same way
# (context: .., dockerfile: environments/Dockerfile).
FROM python:3.12-slim

WORKDIR /opt/convoy

COPY core/ core/
COPY environments/ environments/

# psycopg2-binary is installed explicitly: the service's SQLAlchemy engine
# speaks postgresql+psycopg2:// (see convoy_environments/db/base.py) but the
# driver is not a declared dependency of the package.
RUN pip install --no-cache-dir ./core ./environments psycopg2-binary

EXPOSE 8780

# GET on the POST-only /gateway/mcp route returns 405 — any HTTP response (as
# opposed to a connection error) proves uvicorn is up and the app is mounted.
# http.client does not raise on non-2xx, so no exception handling is needed.
HEALTHCHECK --interval=2s --timeout=5s --retries=30 \
    CMD python -c "import http.client; c = http.client.HTTPConnection('127.0.0.1', 8780, timeout=3); c.request('GET', '/gateway/mcp'); c.getresponse()"

# Dev entrypoint: create tables (create_all is a no-op for existing tables;
# production uses alembic), then serve /gateway/* and /console/* on 8780.
CMD ["sh", "-c", "convoy-environments init-db && exec convoy-environments serve"]
