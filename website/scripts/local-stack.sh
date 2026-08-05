#!/usr/bin/env bash
# Native approximation of the runtime's `make e2e-up` compose stack for
# environments without a Docker daemon: PostgreSQL on :5433, Temporal dev
# server on :7233, an S3-compatible store on :9000 (moto), and the runtime's
# stub environment registry on :8902. Idempotent; safe to re-run. Pair with
# scripts/dev-runtime.sh to start the runtime worker + control plane, and
# scripts/dev-runtime.sh's env for the website itself.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE_DIR="${LOCAL_STACK_STATE:-$HOME/.convoy-local-stack}"
mkdir -p "$STATE_DIR"

# --- PostgreSQL (:5433, runtime projections + website database) ------------
PG_CONF=/etc/postgresql/16/main/postgresql.conf
if [ -f "$PG_CONF" ]; then
  sed -i "s/^#\?port = .*/port = 5433/" "$PG_CONF"
  pg_ctlcluster 16 main start 2>/dev/null || true
  until pg_isready -h localhost -p 5433 -q; do sleep 0.5; done
  sudo -u postgres psql -p 5433 <<'SQL'
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'convoy_admin') THEN
    CREATE ROLE convoy_admin LOGIN PASSWORD 'convoy_admin' CREATEDB CREATEROLE;
  END IF;
END $$;
SELECT 'CREATE DATABASE convoy OWNER convoy_admin'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'convoy') \gexec
SELECT 'CREATE DATABASE convoy_website OWNER convoy_admin'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'convoy_website') \gexec
SQL
  PGPASSWORD=convoy_admin psql -h localhost -p 5433 -U convoy_admin -d convoy \
    -f "$REPO_ROOT/agent-runtime/src/convoy_runtime/projections/schema.sql" >/dev/null
  echo "postgres ready on :5433 (databases convoy, convoy_website)"
fi

# --- Temporal dev server (:7233) -------------------------------------------
if ! curl -s -o /dev/null "http://localhost:8233"; then
  nohup temporal server start-dev --ip 0.0.0.0 --port 7233 --ui-port 8233 \
    --db-filename "$STATE_DIR/temporal.db" \
    > "$STATE_DIR/temporal.log" 2>&1 &
  echo $! > "$STATE_DIR/temporal.pid"
fi
until temporal operator cluster health --address 127.0.0.1:7233 >/dev/null 2>&1; do sleep 0.5; done
echo "temporal ready on :7233"

# --- S3-compatible store (:9000, moto) -------------------------------------
if ! curl -s -o /dev/null "http://localhost:9000"; then
  nohup "$HOME/.local/bin/moto_server" -H 0.0.0.0 -p 9000 \
    > "$STATE_DIR/moto.log" 2>&1 &
  echo $! > "$STATE_DIR/moto.pid"
fi
until curl -s -o /dev/null "http://localhost:9000"; do sleep 0.5; done
echo "object store ready on :9000"

# --- Stub environment registry (:8902) -------------------------------------
if ! curl -s -o /dev/null "http://localhost:8902/health"; then
  DATA_PLANE_URL="http://localhost:8902" nohup python3 \
    "$REPO_ROOT/agent-runtime/docker/stub-env/server.py" \
    > "$STATE_DIR/stub-env.log" 2>&1 &
  echo $! > "$STATE_DIR/stub-env.pid"
fi
until curl -s -o /dev/null "http://localhost:8902/health"; do sleep 0.5; done
echo "stub environment registry ready on :8902"

echo "local stack up"
