# syntax=docker/dockerfile:1.7
# Pinned Convoy API service. The public UI is the sibling Next.js website.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.8.24@sha256:1d31be550ff927957472b2a491dc3de1ea9b5c2d319a9cea5b6a48021e2990a6
FROM ${UV_IMAGE} AS uv
# ---------------------------------------------------------------- stage 1b: pinned SQLite -----------
# The interpreter's stdlib `sqlite3` links the system libsqlite3.so.0 dynamically (Debian trixie ships
# 3.46.1, which predates the WAL-reset fix). This stage compiles the PINNED official amalgamation
# (deploy/compose/sqlite.pin: version, official ZIP, archive SHA3-256 + SHA-256 computed by the operator
# from the verified bytes, and the official sqlite3.c SHA3-256) into a shared library with the same
# soname, installed under /usr/local so that the ONE native library every accessor uses (stdlib sqlite3,
# hence SQLAlchemy, api, worker, migrate, restore, backup CLI) is the pinned one. The server stage
# attests version AND sqlite_source_id() at build time. Pinned by default; an EMPTY SQLITE_VERSION
# (legacy compatibility build only, never the qualifying image) keeps the distribution library.
FROM ${PYTHON_IMAGE} AS sqlite
ARG SQLITE_VERSION="3.51.3"
ARG SQLITE_ARCHIVE_URL="https://www.sqlite.org/2026/sqlite-amalgamation-3510300.zip"
ARG SQLITE_ARCHIVE_SHA3_256="ced02ff9738970f338c9c8e269897b554bcda73f6cf1029d49459e1324dbeaea"
ARG SQLITE_ARCHIVE_SHA256="acb1e6f5d832484bf6d32b681e858c38add8b2acdfd42ac5df24b8afb46552b4"
ARG SQLITE_SQLITE3_C_SHA3_256="32d5424f97e0a7fc5ed2f6335afbb58be4e0298bd7117a34e39d345ff13d859e"
# Compile options follow the distribution library's feature set (what the interpreter's module was built
# against) and appear verbatim in PRAGMA compile_options for the attestation.
ARG SQLITE_CFLAGS="-O2 -DNDEBUG -DSQLITE_THREADSAFE=1 -DSQLITE_ENABLE_COLUMN_METADATA -DSQLITE_ENABLE_DBSTAT_VTAB -DSQLITE_ENABLE_FTS3 -DSQLITE_ENABLE_FTS3_PARENTHESIS -DSQLITE_ENABLE_FTS4 -DSQLITE_ENABLE_FTS5 -DSQLITE_ENABLE_RTREE -DSQLITE_ENABLE_UNLOCK_NOTIFY -DSQLITE_ENABLE_UPDATE_DELETE_LIMIT -DSQLITE_SECURE_DELETE -DSQLITE_MAX_VARIABLE_NUMBER=250000"
RUN set -eu; mkdir -p /opt/sqlite; \
    if [ -n "$SQLITE_VERSION" ]; then \
      for v in SQLITE_ARCHIVE_URL SQLITE_ARCHIVE_SHA3_256 SQLITE_ARCHIVE_SHA256 SQLITE_SQLITE3_C_SHA3_256; do \
        eval "val=\${$v}"; test -n "$val" || { echo "$v is required with SQLITE_VERSION=$SQLITE_VERSION (deploy/compose/sqlite.pin)" >&2; exit 1; }; \
      done; \
      apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev curl ca-certificates openssl unzip && rm -rf /var/lib/apt/lists/*; \
      curl -fsSL "$SQLITE_ARCHIVE_URL" -o /tmp/sqlite.zip; \
      got3="$(openssl dgst -sha3-256 /tmp/sqlite.zip | awk '{print $NF}')"; got2="$(openssl dgst -sha256 /tmp/sqlite.zip | awk '{print $NF}')"; \
      [ "$got3" = "$SQLITE_ARCHIVE_SHA3_256" ] || { echo "archive SHA3-256 mismatch: got $got3" >&2; exit 1; }; \
      [ "$got2" = "$SQLITE_ARCHIVE_SHA256" ] || { echo "archive SHA-256 mismatch: got $got2" >&2; exit 1; }; \
      mkdir -p /tmp/sqlite && unzip -q /tmp/sqlite.zip -d /tmp/sqlite && cd /tmp/sqlite/sqlite-amalgamation-*; \
      gotc="$(openssl dgst -sha3-256 sqlite3.c | awk '{print $NF}')"; \
      [ "$gotc" = "$SQLITE_SQLITE3_C_SHA3_256" ] || { echo "sqlite3.c SHA3-256 mismatch (official digest): got $gotc" >&2; exit 1; }; \
      grep -q "define SQLITE_VERSION *\"$SQLITE_VERSION\"" sqlite3.h || { echo "sqlite3.h does not declare SQLITE_VERSION $SQLITE_VERSION" >&2; exit 1; }; \
      mkdir -p /opt/sqlite/lib /opt/sqlite/include /opt/sqlite/bin; \
      gcc $SQLITE_CFLAGS -fPIC -shared -Wl,-soname,libsqlite3.so.0 -o /opt/sqlite/lib/libsqlite3.so.0.8.6 sqlite3.c -lm -ldl -lpthread; \
      ln -s libsqlite3.so.0.8.6 /opt/sqlite/lib/libsqlite3.so.0; ln -s libsqlite3.so.0.8.6 /opt/sqlite/lib/libsqlite3.so; \
      gcc $SQLITE_CFLAGS -o /opt/sqlite/bin/sqlite3 shell.c -L/opt/sqlite/lib -lsqlite3 -lm -ldl -lpthread; \
      cp sqlite3.h sqlite3ext.h /opt/sqlite/include/; \
      printf '%s\n' "$SQLITE_VERSION" > /opt/sqlite/PINNED_VERSION; \
      printf 'archive_sha3_256=%s\narchive_sha256=%s\nsqlite3_c_sha3_256=%s\n' "$SQLITE_ARCHIVE_SHA3_256" "$SQLITE_ARCHIVE_SHA256" "$SQLITE_SQLITE3_C_SHA3_256" > /opt/sqlite/PINNED_DIGESTS; \
    fi

# ---------------------------------------------------------------- stage 2: server -------------------
FROM ${PYTHON_IMAGE} AS server
ARG SQLITE_VERSION="3.51.3"
ARG SQLITE_SOURCE_ID="2026-03-13 10:38:09 737ae4a34738ffa0c3ff7f9bb18df914dd1cad163f28fd6b6e114a344fe6d618"
COPY --from=uv /uv /uvx /bin/
# pinned SQLite (empty when no pin was supplied) under /usr/local. Loader precedence is EXPLICIT, not
# assumed: the dynamic loader resolves `libsqlite3.so.0` (NEEDED by the interpreter's `_sqlite3`) through
# ld.so.cache, whose order follows /etc/ld.so.conf.d/*.conf read in byte order. On amd64 "libc.conf"
# (/usr/local/lib) sorts before "x86_64-linux-gnu.conf", so the pinned library won by accident; on arm64
# "aarch64-linux-gnu.conf" sorts BEFORE "libc.conf" and lists /lib/aarch64-linux-gnu first, so the
# distribution 3.46.1 won and the build-time attestation failed (root's ARM64 build of 354ca78). A
# "00-" entry sorts before every distribution entry on every architecture; ldconfig is then checked to
# resolve the soname to /usr/local/lib FIRST, and the attestation below proves the actual mapping in the
# interpreter rather than trusting the cache. Legacy build (empty pin): no entry, distribution library.
COPY --from=sqlite /opt/sqlite/ /usr/local/
RUN set -eu; \
    if [ -n "$SQLITE_VERSION" ]; then \
      printf '%s\n' '# Convoy: the pinned SQLite in /usr/local/lib precedes every distribution directory' \
        '# (ld.so.conf.d is read in byte order; "00-" sorts before the multiarch and libc entries).' \
        '/usr/local/lib' > /etc/ld.so.conf.d/00-convoy-pinned-sqlite.conf; \
    fi; \
    ldconfig; \
    if [ -n "$SQLITE_VERSION" ]; then \
      first="$(ldconfig -p | awk '$1 == "libsqlite3.so.0" {print $NF; exit}')"; \
      echo "ld.so.cache resolves libsqlite3.so.0 first to: $first"; ldconfig -p | grep 'libsqlite3\.so' || true; \
      [ "$first" = "/usr/local/lib/libsqlite3.so.0" ] || { echo "loader precedence: the pinned library is not first" >&2; exit 1; }; \
    fi
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3 \
    PATH="/app/.venv/bin:${PATH}" \
    CONVOY_DATA_DIR=/data \
    CONVOY_BACKUP_DIR=/data/backups \
    CONVOY_WEB_DIST=/app/server/convoy_server/web_dist

RUN groupadd --system --gid 10001 convoy \
 && useradd --system --uid 10001 --gid convoy --home-dir /app --shell /usr/sbin/nologin convoy \
 && mkdir -p /app /data /data/backups /run/execution-signing \
 && chmod 700 /run/execution-signing \
 && chown -R convoy:convoy /app /data /run/execution-signing

WORKDIR /app
# The workspace is installed editable (uv's default for members), so the sources stay in the image;
# that keeps `convoy_server.routers.evidence` able to find docs/JETSON_GUIDE.md at /app/docs.
COPY --chown=convoy:convoy pyproject.toml uv.lock ./
COPY --chown=convoy:convoy server/ ./server/
COPY --chown=convoy:convoy agent/ ./agent/
COPY --chown=convoy:convoy contracts/ ./contracts/
COPY --chown=convoy:convoy worker/ ./worker/
COPY --chown=convoy:convoy docs/JETSON_GUIDE.md ./docs/JETSON_GUIDE.md
# The venv is built on the image's own CPython (never a downloaded interpreter, whose `_sqlite3`
# could carry a statically linked SQLite) and that is verified, not assumed.
RUN uv sync --frozen --no-dev --package convoy-server --python /usr/local/bin/python3 \
 && rm -rf /root/.cache \
 && test "$(readlink -f /app/.venv/bin/python)" = "$(readlink -f /usr/local/bin/python3)"
# Build-time attestation of the native SQLite every accessor uses: version, exact source id, WAL
# qualification, the library FILE the interpreter actually mapped (must live under /usr/local/lib) and
# the interpreter's base prefix (the image's CPython). Fails the build otherwise; the JSON stays in the
# image as evidence. `ldd` of the extension module is printed as loader-side evidence.
RUN set -eu; \
    ext="$(python -c 'import _sqlite3; print(_sqlite3.__file__)')"; echo "$ext"; ldd "$ext" | grep -i sqlite || true; \
    if [ -n "$SQLITE_VERSION" ]; then convoy-server sqlite-attest --expect "$SQLITE_VERSION" --expect-source-id "$SQLITE_SOURCE_ID" --require-wal-safe --expect-library-prefix /usr/local/lib/ --expect-base-prefix /usr/local > /app/sqlite-attestation.json; \
    else convoy-server sqlite-attest --expect-base-prefix /usr/local > /app/sqlite-attestation.json; fi; \
    cat /app/sqlite-attestation.json; chown convoy:convoy /app/sqlite-attestation.json


USER convoy
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3).status == 200 else 1)"
# Same image serves the API and the worker: override `command` in compose.
CMD ["convoy-server", "serve", "--host", "0.0.0.0", "--port", "8080"]
