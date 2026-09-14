#!/usr/bin/env bash
# Convoy: register a runtime archive built by build-llama-cpp.sh as a runtime artifact for a build
# recipe, either by uploading it to the server (fleet) or by installing it into this device's agent
# cache (device-scoped). Prints the artifact id.
#
# STATUS: pending device verification on a Jetson. Exercised in software against a local convoy-server
# with a placeholder archive (upload, register, idempotent re-run, device-cache install); shellcheck-clean.
#
# Usage:
#   scripts/jetson/register-runtime.sh --server https://convoy.example.com --recipe rcp_xxx \
#       --archive ~/convoy-build/out/runtime-<commit>-aarch64.tar.gz --receipt ~/convoy-build/out/receipt.json \
#       [--scope fleet|device:<device_id>] [--storage server|device] [--token-file FILE] [--ca-file FILE] \
#       [--allow-tuple-mismatch] [--agent-dir /var/lib/convoy-agent] [--agent-user convoy-agent]
#
# Auth: an operator/admin API token (`cva_...`, Settings -> API tokens) read from CONVOY_API_TOKEN or
# --token-file. The token reaches curl through a private (0600) config file, never on the command line.
#
# Flow (docs/API.md "Catalog"):
#   0. GET /api/v1/recipes -> the recipe's commit and platform tuple must match the receipt's provenance
#      (target_observed written by build-llama-cpp.sh). A tuple mismatch is refused unless
#      --allow-tuple-mismatch is given here AND the receipt says tuple_mismatch_allowed (the server may
#      still refuse). A commit mismatch is always refused.
#   1a. --storage server (default): POST /api/v1/runtime-artifacts/upload (Content-Type: application/gzip)
#       -> {archive_sha256, archive_size, members}; must equal the receipt's archive_sha256/size.
#   1b. --storage device: no upload. The archive is installed, verified by sha256, into THIS device's
#       agent cache at <agent-dir>/cache/runtime/<archive_sha256>.tar.gz owned by the service user, which
#       is exactly where agent/convoy_agent/executor.py looks before attempting a download (the server
#       returns 404 for device-stored artifacts). The scope must be device:<this device id> (read from
#       <agent-dir>/agent.json when --scope is omitted). Requires root or the service user.
#   2. POST /api/v1/runtime-artifacts {recipe_id, receipt, scope, storage} -> {id, ...}
# Idempotent: re-running with the same bytes returns the already-registered artifact id.
set -euo pipefail

SERVER=""; RECIPE=""; ARCHIVE=""; RECEIPT=""; SCOPE=""; STORAGE="server"; TOKEN_FILE=""; CA_FILE=""
ALLOW_MISMATCH=0; AGENT_DIR="${CONVOY_AGENT_DIR:-/var/lib/convoy-agent}"; AGENT_USER="convoy-agent"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --server) SERVER="${2%/}"; shift 2 ;;
    --recipe) RECIPE="$2"; shift 2 ;;
    --archive) ARCHIVE="$2"; shift 2 ;;
    --receipt) RECEIPT="$2"; shift 2 ;;
    --scope) SCOPE="$2"; shift 2 ;;
    --storage) STORAGE="$2"; shift 2 ;;
    --token-file) TOKEN_FILE="$2"; shift 2 ;;
    --ca-file) CA_FILE="$2"; shift 2 ;;
    --allow-tuple-mismatch) ALLOW_MISMATCH=1; shift ;;
    --agent-dir) AGENT_DIR="$2"; shift 2 ;;
    --agent-user) AGENT_USER="$2"; shift 2 ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$SERVER" && -n "$RECIPE" && -n "$RECEIPT" && -n "$ARCHIVE" ]] || { echo "need --server, --recipe, --archive, --receipt" >&2; exit 2; }
[[ "$STORAGE" == "server" || "$STORAGE" == "device" ]] || { echo "--storage must be server|device" >&2; exit 2; }
[[ "$SERVER" == https://* ]] || echo "[warning] server URL is not https; only use plain http on a trusted LAN" >&2
[[ -r "$RECEIPT" ]] || { echo "receipt not readable: $RECEIPT" >&2; exit 2; }
[[ -r "$ARCHIVE" ]] || { echo "archive not readable: $ARCHIVE" >&2; exit 2; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 2; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 2; }

if [[ -n "$TOKEN_FILE" ]]; then
  TOKEN="$(tr -d '\r\n' < "$TOKEN_FILE")"
else
  TOKEN="${CONVOY_API_TOKEN:-}"
fi
[[ -n "$TOKEN" ]] || { echo "set CONVOY_API_TOKEN or pass --token-file (a cva_ API token)" >&2; exit 2; }
[[ "$TOKEN" == cva_* ]] || echo "[warning] token does not start with cva_; API tokens created in Settings do" >&2

# curl reads the Authorization header from a 0600 config file so the secret never appears in argv
# (stdin stays free for request bodies). The file is removed on exit.
CURL_CFG="$(mktemp)"
chmod 0600 "$CURL_CFG"
printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" > "$CURL_CFG"
[[ -n "$CA_FILE" ]] && printf 'cacert = "%s"\n' "$CA_FILE" >> "$CURL_CFG"
TMP_FILES=("$CURL_CFG")
cleanup() { rm -f "${TMP_FILES[@]}"; }
trap cleanup EXIT
curl_auth() { curl -sS --fail-with-body -K "$CURL_CFG" "$@"; }
jget() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1]))
for k in sys.argv[2].split("."):
    d = d.get(k) if isinstance(d, dict) else None
print("" if d is None else d)' "$1" "$2"; }

RECEIPT_SHA="$(jget "$RECEIPT" archive_sha256)"
RECEIPT_SIZE="$(jget "$RECEIPT" archive_size)"
echo "==> receipt: archive_sha256=$RECEIPT_SHA size=$RECEIPT_SIZE"
LOCAL_SHA="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
LOCAL_SIZE="$(stat -c %s "$ARCHIVE")"
[[ "$LOCAL_SHA" == "$RECEIPT_SHA" && "$LOCAL_SIZE" == "$RECEIPT_SIZE" ]] \
  || { echo "archive on disk ($LOCAL_SHA, $LOCAL_SIZE bytes) does not match the receipt; rebuild" >&2; exit 1; }
echo "   [verified] local archive matches the receipt"

# ---------------------------------------------------------------- 0. recipe provenance check --------
echo "==> recipe $RECIPE: commit and platform tuple vs receipt provenance"
RECIPE_JSON="$(mktemp)"; TMP_FILES+=("$RECIPE_JSON")
curl_auth "$SERVER/api/v1/recipes" -o "$RECIPE_JSON" || { echo "could not list recipes" >&2; exit 1; }
set +e
TUPLE_REPORT="$(RECIPE="$RECIPE" ALLOW="$ALLOW_MISMATCH" python3 - "$RECIPE_JSON" "$RECEIPT" <<'PY'
import json, os, sys
rows = json.load(open(sys.argv[1]))
rows = rows if isinstance(rows, list) else rows.get("items", [])
rec = next((r for r in rows if r.get("id") == os.environ["RECIPE"]), None)
if rec is None:
    print("recipe not found on the server"); sys.exit(1)
prov = json.load(open(sys.argv[2])).get("provenance") or {}
if prov.get("commit") and rec.get("commit") != prov.get("commit"):
    print(f"commit mismatch: recipe {rec.get('commit')} vs receipt {prov.get('commit')}"); sys.exit(1)
target = rec.get("target") or {}
obs = prov.get("target_observed")
if not obs:
    print("receipt has no provenance.target_observed (built with an older script); rebuild with the current build-llama-cpp.sh"); sys.exit(1)
diffs = [f"{k}: recipe={target.get(k)!r} observed={obs.get(k)!r}" for k in ("arch", "l4t", "cuda") if target.get(k) and target.get(k) != obs.get(k)]
if not diffs and not prov.get("tuple_mismatch_allowed"):
    print(f"tuple matches: arch={obs.get('arch')} l4t={obs.get('l4t')} cuda={obs.get('cuda')} (recipe track {rec.get('track') or 'n/a'}, receipt track {prov.get('track') or 'n/a'})"); sys.exit(0)
msg = "; ".join(diffs) or "receipt was built with --allow-tuple-mismatch"
if os.environ["ALLOW"] == "1" and prov.get("tuple_mismatch_allowed"):
    print(f"[warning] tuple mismatch accepted with --allow-tuple-mismatch: {msg}"); sys.exit(0)
print(f"tuple mismatch: {msg}. Rebuild on the pinned tuple, or build AND register with --allow-tuple-mismatch"); sys.exit(1)
PY
)"; TUPLE_RC=$?
set -e
echo "   $TUPLE_REPORT"
[[ "$TUPLE_RC" -eq 0 ]] || exit 1
[[ "$TUPLE_REPORT" == "tuple matches"* ]] && echo "   [verified] recipe commit and platform tuple match the receipt"

# ---------------------------------------------------------------- 1. bytes: upload or local install --
if [[ "$STORAGE" == "server" ]]; then
  [[ -n "$SCOPE" ]] || SCOPE="fleet"
  echo "==> upload $ARCHIVE -> $SERVER/api/v1/runtime-artifacts/upload (application/gzip)"
  UPLOAD_OUT="$(mktemp)"; TMP_FILES+=("$UPLOAD_OUT")
  curl_auth -X POST -H "Content-Type: application/gzip" -H "Expect:" \
    --data-binary "@$ARCHIVE" -o "$UPLOAD_OUT" "$SERVER/api/v1/runtime-artifacts/upload" \
    || { echo "upload failed:"; cat "$UPLOAD_OUT"; echo; exit 1; }
  SERVER_SHA="$(jget "$UPLOAD_OUT" archive_sha256)"
  SERVER_SIZE="$(jget "$UPLOAD_OUT" archive_size)"
  [[ "$SERVER_SHA" == "$RECEIPT_SHA" && "$SERVER_SIZE" == "$RECEIPT_SIZE" ]] \
    || { echo "server computed $SERVER_SHA/$SERVER_SIZE, receipt says $RECEIPT_SHA/$RECEIPT_SIZE" >&2; exit 1; }
  echo "   [verified] server-side sha256 and size equal the receipt"
else
  # Device-scoped: bytes never leave this device. Install into the agent cache where the executor
  # looks (cache/runtime/<sha>.tar.gz); extraction re-verifies every member against the registered receipt.
  if [[ -z "$SCOPE" ]]; then
    [[ -r "$AGENT_DIR/agent.json" ]] || { echo "--scope device:<id> not given and $AGENT_DIR/agent.json not readable (run as root or $AGENT_USER)" >&2; exit 2; }
    DEV_ID="$(jget "$AGENT_DIR/agent.json" device_id)"
    [[ -n "$DEV_ID" ]] || { echo "this device is not enrolled ($AGENT_DIR/agent.json has no device_id)" >&2; exit 2; }
    SCOPE="device:$DEV_ID"
  fi
  [[ "$SCOPE" == device:* ]] || { echo "--storage device requires --scope device:<device_id> (got '$SCOPE')" >&2; exit 2; }
  if [[ -r "$AGENT_DIR/agent.json" ]]; then
    LOCAL_ID="$(jget "$AGENT_DIR/agent.json" device_id)"
    [[ "$SCOPE" == "device:$LOCAL_ID" ]] || { echo "scope $SCOPE is not this device (device:$LOCAL_ID); a device-stored artifact can only serve the device holding the bytes" >&2; exit 2; }
  fi
  CACHE_DIR="$AGENT_DIR/cache/runtime"
  DEST="$CACHE_DIR/$RECEIPT_SHA.tar.gz"
  echo "==> install $ARCHIVE -> $DEST (owner $AGENT_USER)"
  if [[ "$(id -u)" -ne 0 && "$(id -un)" != "$AGENT_USER" ]]; then
    echo "installing into $AGENT_DIR needs root or the $AGENT_USER user" >&2; exit 2
  fi
  install -d -m 0700 "$AGENT_DIR/cache" "$CACHE_DIR"
  TMP_DEST="$CACHE_DIR/.$RECEIPT_SHA.part"
  cp "$ARCHIVE" "$TMP_DEST"
  INSTALLED_SHA="$(sha256sum "$TMP_DEST" | awk '{print $1}')"
  [[ "$INSTALLED_SHA" == "$RECEIPT_SHA" ]] || { rm -f "$TMP_DEST"; echo "copy verification failed ($INSTALLED_SHA)" >&2; exit 1; }
  chmod 0600 "$TMP_DEST"
  mv -f "$TMP_DEST" "$DEST"
  if [[ "$(id -u)" -eq 0 ]]; then chown -R "$AGENT_USER:$AGENT_USER" "$AGENT_DIR/cache"; fi
  sync
  [[ "$(stat -c %U "$DEST")" == "$AGENT_USER" ]] || { echo "$DEST is not owned by $AGENT_USER" >&2; exit 1; }
  echo "   [verified] $DEST present, sha256 matches the receipt, owned by $AGENT_USER"
fi

# ---------------------------------------------------------------- 2. register the receipt ----------
echo "==> register receipt for recipe $RECIPE (scope=$SCOPE storage=$STORAGE)"
BODY="$(RECIPE="$RECIPE" SCOPE="$SCOPE" STORAGE="$STORAGE" python3 - "$RECEIPT" <<'PY'
import json, os, sys
receipt = json.load(open(sys.argv[1]))
print(json.dumps({"recipe_id": os.environ["RECIPE"], "receipt": receipt,
                  "scope": os.environ["SCOPE"], "storage": os.environ["STORAGE"]}))
PY
)"
REG_OUT="$(mktemp)"; TMP_FILES+=("$REG_OUT" "$REG_OUT.code")
set +e
printf '%s' "$BODY" | curl_auth -X POST -H "Content-Type: application/json" --data-binary @- \
  -w '%{http_code}' -o "$REG_OUT" "$SERVER/api/v1/runtime-artifacts" > "$REG_OUT.code"
CURL_RC=$?
set -e
HTTP_CODE="$(cat "$REG_OUT.code" 2>/dev/null || echo "")"
if [[ "$CURL_RC" -eq 0 && "$HTTP_CODE" == 2* ]]; then
  ART_ID="$(jget "$REG_OUT" id)"
  VERIFIED="$(jget "$REG_OUT" verified)"
  echo "   [verified] registered runtime artifact $ART_ID (verified=$VERIFIED)"
elif [[ "$HTTP_CODE" == "409" ]] && grep -q "already registered" "$REG_OUT"; then
  echo "   already registered for this scope; looking up the existing artifact id"
  ART_ID="$(curl_auth "$SERVER/api/v1/runtime-artifacts" | SHA="$RECEIPT_SHA" SCOPE="$SCOPE" python3 -c '
import json, os, sys
rows = json.load(sys.stdin)
rows = rows if isinstance(rows, list) else rows.get("items", [])
m = [r for r in rows if r.get("archive_sha256") == os.environ["SHA"] and r.get("scope") == os.environ["SCOPE"]]
print(m[0]["id"] if m else "")')"
  [[ -n "$ART_ID" ]] || { echo "could not find the existing artifact" >&2; exit 1; }
else
  echo "registration failed (HTTP ${HTTP_CODE:-?}):" >&2; cat "$REG_OUT" >&2; echo >&2
  exit 1
fi
echo
echo "artifact_id=$ART_ID"
[[ "$STORAGE" == "device" ]] && echo "note: device-stored artifact (receipt_only); only $SCOPE can run it, other devices need a fleet upload"
echo "next: create a NEW release that binds recipe $RECIPE + artifact $ART_ID (releases are immutable; a build_required release is never mutated)"
