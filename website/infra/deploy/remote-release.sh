#!/usr/bin/env bash
# Runs on the Lightsail instance as root. Installs one built release of the
# landing page next to the existing compose project and points the `web`
# service at it. Nothing else in the project (postgres, notifier, caddy, the
# override file) is touched, so the previous console stays available for
# rollback: its image is still in ECR and cached locally, and every file this
# script changes is copied to rollback/<timestamp>/ first.
#
#   remote-release.sh deploy <sha> /tmp/release-<sha>.tar.gz
#   remote-release.sh rollback            # restore the newest backup
set -euo pipefail

APP_DIR=/opt/convoy
RELEASES="$APP_DIR/releases"
ROLLBACK="$APP_DIR/rollback"
MODE="${1:-}"
cd "$APP_DIR"

health() {
  # Ask through the compose network, the same way Caddy reaches the app.
  for _ in $(seq 1 30); do
    if docker compose exec -T caddy wget -qO- --timeout=5 http://web:3000/ 2>/dev/null | grep -q "<title>Convoy"; then
      return 0
    fi
    sleep 2
  done
  return 1
}

backup() {
  local dir="$ROLLBACK/$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$dir"
  for f in compose.yaml compose.override.yaml .env Caddyfile; do
    [ -f "$f" ] && cp -p "$f" "$dir/"
  done
  docker compose images web >"$dir/web-image.txt" 2>/dev/null || true
  chmod 700 "$dir"
  echo "$dir"
}

restore_from() {
  local dir="$1"
  test -f "$dir/compose.yaml" || { echo "no compose.yaml in $dir"; exit 1; }
  cp -p "$dir/compose.yaml" compose.yaml
  [ -f "$dir/compose.override.yaml" ] && cp -p "$dir/compose.override.yaml" compose.override.yaml
  # The previous console image lives in ECR; re-login in case the local copy
  # was pruned. The instance credentials are scoped to exactly this pull.
  if grep -q 'dkr.ecr' compose.yaml; then
    REGISTRY=$(sed -nE 's#.*image: ([0-9]+\.dkr\.ecr\.[^/]+)/.*#\1#p' compose.yaml | head -1)
    REGION=$(echo "$REGISTRY" | sed -nE 's#.*\.dkr\.ecr\.([a-z0-9-]+)\.amazonaws\.com#\1#p')
    aws ecr get-login-password --region "$REGION" </dev/null | docker login --username AWS --password-stdin "$REGISTRY" </dev/null || true
  fi
  docker compose pull web </dev/null
  docker compose up -d --force-recreate web </dev/null
}

case "$MODE" in
  deploy)
    SHA="${2:?sha}"; TARBALL="${3:?tarball}"
    test -f "$TARBALL" || { echo "missing $TARBALL"; exit 1; }
    FREE_KB=$(df --output=avail -k "$APP_DIR" | tail -1)
    [ "$FREE_KB" -gt 2000000 ] || { echo "less than 2 GB free on $APP_DIR (${FREE_KB} KB)"; exit 1; }
    python3 -c "import yaml" 2>/dev/null || { apt-get update -qq && apt-get install -y -qq python3-yaml; }

    DEST="$RELEASES/$SHA"
    rm -rf "$DEST"; mkdir -p "$DEST"
    tar -xzf "$TARBALL" -C "$DEST"
    test -f "$DEST/server.js" || { echo "release has no server.js"; exit 1; }
    chmod -R a+rX "$DEST"
    rm -f "$TARBALL"

    BACKUP=$(backup)
    echo "backup: $BACKUP"

    python3 - "$SHA" <<'PYEOF'
import sys, yaml
sha = sys.argv[1]
with open("compose.yaml") as f:
    doc = yaml.safe_load(f)
doc.setdefault("services", {})["web"] = {
    "image": "node:22-alpine",
    "restart": "unless-stopped",
    "working_dir": "/app",
    "command": ["node", "server.js"],
    "user": "node",
    "environment": {"NODE_ENV": "production", "PORT": "3000", "HOSTNAME": "0.0.0.0", "NEXT_TELEMETRY_DISABLED": "1"},
    "volumes": [f"./releases/{sha}:/app:ro"],
    "expose": ["3000"],
}
with open("compose.yaml", "w") as f:
    yaml.safe_dump(doc, f, sort_keys=False)
PYEOF

    docker compose pull web </dev/null
    docker compose up -d --force-recreate --no-deps web </dev/null
    if health; then
      echo "healthy: web serves release $SHA"
    else
      echo "::error::new release failed its health check; restoring $BACKUP"
      docker compose logs --tail 60 web </dev/null || true
      restore_from "$BACKUP"
      exit 1
    fi
    # Keep the newest three releases on disk (the active one is among them).
    ls -1dt "$RELEASES"/*/ 2>/dev/null | tail -n +4 | grep -v "/$SHA/" | xargs -r rm -rf
    docker compose ps </dev/null
    ;;
  rollback)
    LATEST=$(ls -1d "$ROLLBACK"/*/ 2>/dev/null | sort | tail -1)
    test -n "$LATEST" || { echo "no backups under $ROLLBACK"; exit 1; }
    echo "restoring $LATEST"
    restore_from "$LATEST"
    health && echo "healthy after rollback" || { echo "::error::web is not healthy after rollback"; docker compose logs --tail 60 web </dev/null; exit 1; }
    docker compose ps </dev/null
    ;;
  *)
    echo "usage: $0 deploy <sha> <tarball> | rollback"; exit 2 ;;
esac
