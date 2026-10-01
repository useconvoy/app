#!/usr/bin/env bash
# Change only web/control-plane. Retain runtime env, SQLite data and unrelated services.
# deploy <sha> <standalone.tar.gz> [<api-image.tar.gz>]; rollback [backup-name]
set -euo pipefail
umask 077
APP_DIR="${CONVOY_APP_DIR:-/opt/convoy}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_TOOL="$SCRIPT_DIR/compose-release.py"
RELEASES="$APP_DIR/releases"
ROLLBACK="$APP_DIR/rollback"
MODE="${1:-}"
cd "$APP_DIR"
test -f compose.yaml
test -f "$COMPOSE_TOOL"
python3 -c 'import yaml' || { echo 'python3-yaml is required on the deployment host' >&2; exit 1; }
mkdir -p "$RELEASES" "$ROLLBACK"
exec 9>"$APP_DIR/.release.lock"
flock -n 9 || { echo 'another deployment is running' >&2; exit 1; }

has_api() { [ -n "$(python3 "$COMPOSE_TOOL" image compose.yaml control-plane)" ]; }
has_evaluations() { [ -n "$(python3 "$COMPOSE_TOOL" image compose.yaml evaluations)" ]; }
has_portal() { [ "$(python3 "$COMPOSE_TOOL" portal compose.yaml)" = yes ]; }
health() {
  local body api_body portal_body
  for _ in $(seq 1 30); do
    body=$(docker compose exec -T caddy wget -qO- --timeout=5 http://web:3000/ 2>/dev/null </dev/null || true)
    api_body='"ok":true'
    portal_body='"authenticated":false'
    if has_api; then
      api_body=$(docker compose exec -T caddy wget -qO- --timeout=5 http://control-plane:8080/api/health 2>/dev/null </dev/null || true)
    fi
    if has_portal; then
      portal_body=$(docker compose exec -T caddy wget -qO- --timeout=5 http://web:3000/api/portal/session 2>/dev/null </dev/null || true)
    fi
    if [[ "$body" == *'<title>Convoy'* ]] && [[ "$api_body" == *'"ok":true'* || "$api_body" == *'"ok": true'* ]] && [[ "$portal_body" == *'"authenticated":false'* || "$portal_body" == *'"authenticated": false'* ]]; then return 0; fi
    sleep 2
  done
  return 1
}
backup() {
  local dir
  dir=$(mktemp -d "$ROLLBACK/$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
  for f in compose.yaml compose.override.yaml .env Caddyfile; do
    [ ! -f "$f" ] || cp -p "$f" "$dir/"
  done
  docker compose images web >"$dir/web-image.txt" 2>/dev/null || true
  if has_api; then docker compose images control-plane >"$dir/control-plane-image.txt" 2>/dev/null || true; fi
  echo "$dir"
}
ensure_web_image() {
  local image registry region
  image=$(python3 "$COMPOSE_TOOL" image compose.yaml web)
  # An argumentless return inside Bash 5's ERR trap can inherit the original
  # deployment failure, despite this successful inspection. Recovery needs zero.
  if docker image inspect "$image" >/dev/null 2>&1; then return 0; fi
  if [[ "$image" == *'.dkr.ecr.'* ]]; then
    registry=${image%%/*}
    region=$(printf '%s' "$registry" | sed -nE 's#.*\.dkr\.ecr\.([a-z0-9-]+)\.amazonaws\.com#\1#p')
    aws ecr get-login-password --region "$region" </dev/null | docker login --username AWS --password-stdin "$registry" >/dev/null
  fi
  docker compose pull web </dev/null
}
restore_from() {
  local dir="$1" current_api_id current_evaluations_id image
  test -f "$dir/compose.yaml" || { echo 'backup compose file missing' >&2; return 1; }
  current_api_id=$(docker compose ps -q control-plane 2>/dev/null || true)
  current_evaluations_id=$(docker compose ps -q evaluations 2>/dev/null || true)
  # Restore only application definitions; keep host routing and runtime files.
  python3 "$COMPOSE_TOOL" restore compose.yaml "$dir/compose.yaml" || return 1
  docker compose config --quiet </dev/null || return 1
  if has_api; then
    image=$(python3 "$COMPOSE_TOOL" image compose.yaml control-plane)
    docker image inspect "$image" >/dev/null 2>&1 || { echo "rollback API image missing: $image" >&2; return 1; }
    docker compose up -d --force-recreate --no-deps control-plane </dev/null || return 1
  elif [ -n "$current_api_id" ]; then
    docker rm -f "$current_api_id" >/dev/null || return 1
  fi
  if has_evaluations; then
    docker compose up -d --force-recreate --no-deps evaluations </dev/null || return 1
  elif [ -n "$current_evaluations_id" ]; then
    docker rm -f "$current_evaluations_id" >/dev/null || return 1
  fi
  ensure_web_image || return 1
  docker compose up -d --force-recreate --no-deps web </dev/null
}
case "$MODE" in
  deploy)
    SHA="${2:?sha}"; TARBALL="${3:?tarball}"; API_ARCHIVE="${4:-}"
    [[ "$SHA" =~ ^[a-f0-9]{12,40}$ ]] || { echo 'invalid release id' >&2; exit 1; }
    test -f "$TARBALL"
    test -r portal/web.env || { echo 'portal/web.env must be provisioned before deployment' >&2; exit 1; }
    WITH_API=no
    if [ -n "$API_ARCHIVE" ]; then
      test -f "$API_ARCHIVE"; test -r portal/control-plane.env; test -d portal/data
      WITH_API=yes
    fi
    FREE_KB=$(df --output=avail -k "$APP_DIR" | tail -1)
    [ "$FREE_KB" -gt 2000000 ] || { echo 'less than 2 GB free for deployment' >&2; exit 1; }
    DEST="$RELEASES/$SHA"
    STAGE=$(mktemp -d "$RELEASES/.${SHA}-XXXXXX")
    tar -xzf "$TARBALL" -C "$STAGE"
    test -f "$STAGE/server.js" || { rm -rf "$STAGE"; echo 'release has no server.js' >&2; exit 1; }
    chmod -R a+rX "$STAGE"
    if [ -d "$DEST" ]; then rm -rf "$STAGE"; else mv "$STAGE" "$DEST"; fi
    if [ "$WITH_API" = yes ]; then
      docker load -i "$API_ARCHIVE" >/dev/null
      docker image inspect "convoy-control-plane:$SHA" >/dev/null
    fi
    BACKUP=$(backup)
    echo "backup: $BACKUP"
    recover_failed_deploy() {
      local status=$?
      trap - ERR
      echo 'new release failed; restoring both application services' >&2
      if restore_from "$BACKUP" && health; then
        echo 'previous application services restored; runtime data retained' >&2
      else
        echo "automatic rollback needs operator attention; backup: $BACKUP" >&2
      fi
      exit "$status"
    }
    trap recover_failed_deploy ERR
    python3 "$COMPOSE_TOOL" deploy compose.yaml "$SHA" "$WITH_API"
    docker compose config --quiet </dev/null
    ensure_web_image
    if [ "$WITH_API" = yes ]; then docker compose up -d --force-recreate --no-deps control-plane </dev/null; fi
    if [ "$WITH_API" = yes ] && has_evaluations; then docker compose up -d --force-recreate --no-deps evaluations </dev/null; fi
    docker compose up -d --force-recreate --no-deps web </dev/null
    health
    trap - ERR
    rm -f "$TARBALL"
    [ -z "$API_ARCHIVE" ] || rm -f "$API_ARCHIVE"
    echo "healthy: application release $SHA"
    # Keep prior images and standalone directories so saved rollback targets remain usable.
    docker compose ps </dev/null
    ;;
  rollback)
    if [ -n "${2:-}" ]; then
      [[ "$2" =~ ^[A-Za-z0-9_-]+$ ]] || { echo 'invalid backup name' >&2; exit 1; }
      TARGET="$ROLLBACK/$2"
    else
      TARGET=$(python3 "$COMPOSE_TOOL" newest-backup "$ROLLBACK")
    fi
    test -n "$TARGET" && test -d "$TARGET" || { echo 'backup not found' >&2; exit 1; }
    BEFORE=$(backup)
    echo "pre-rollback backup: $BEFORE"
    if ! restore_from "$TARGET" || ! health; then
      echo 'rollback failed; restoring application services from before rollback' >&2
      restore_from "$BEFORE" && health || echo 'application recovery needs operator attention' >&2
      exit 1
    fi
    echo 'healthy after rollback; runtime data retained'
    docker compose ps </dev/null
    ;;
  *) echo "usage: $0 deploy <sha> <standalone.tar.gz> [api-image.tar.gz] | rollback [backup]" >&2; exit 2 ;;
esac
