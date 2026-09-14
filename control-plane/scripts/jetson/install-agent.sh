#!/usr/bin/env bash
# Convoy: install the device agent on a Jetson Orin Nano (JetPack 6.x, Python 3.10) as a hardened
# systemd service. Run as root (sudo). Idempotent: re-running upgrades the package and unit in place.
#
# STATUS: pending device verification (authored and shellcheck-linted without a Jetson; not executed on
# hardware). Everything it verifies is printed.
#
# Usage:  sudo scripts/jetson/install-agent.sh [--source PATH_OR_WHEEL] [--no-enable]
#   --source   directory containing agent/pyproject.toml (default: the checkout this script lives in)
#              or a prebuilt convoy_agent-*.whl
# After install, enroll once with a token from the Convoy UI (Fleet -> Enroll device), then start:
#   sudo -u convoy-agent /opt/convoy-agent/venv/bin/convoy-agent enroll --server https://<host> --token <t> --name <name>
#   sudo systemctl start convoy-agent
set -euo pipefail

SVC_USER="convoy-agent"
DATA_DIR="/var/lib/convoy-agent"
PREFIX="/opt/convoy-agent"
VENV="$PREFIX/venv"
UNIT_DST="/etc/systemd/system/convoy-agent.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SOURCE="$REPO_ROOT/agent"
UNIT_SRC="$REPO_ROOT/deploy/systemd/convoy-agent.service"
GUIDE_SRC="$REPO_ROOT/docs/JETSON_GUIDE.md"
ENABLE=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE="$2"; shift 2 ;;
    --no-enable) ENABLE=0; shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

log() { printf '\n==> %s\n' "$*"; }
ok()  { printf '   [verified] %s\n' "$*"; }
fail() { printf '   [FAILED] %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || fail "run as root (sudo)"
[[ -r "$UNIT_SRC" ]] || fail "unit file not found: $UNIT_SRC"
if [[ -d "$SOURCE" ]]; then
  [[ -f "$SOURCE/pyproject.toml" ]] || fail "$SOURCE has no pyproject.toml (expected the agent/ directory)"
elif [[ "$SOURCE" != *.whl || ! -f "$SOURCE" ]]; then
  fail "--source must be the agent/ directory or a convoy_agent wheel"
fi

log "python"
command -v python3 >/dev/null || fail "python3 missing (JetPack 6 ships 3.10)"
PY_VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || fail "python3 is $PY_VER; the agent needs >= 3.10"
ok "python3 $PY_VER"
if ! python3 -c 'import venv, ensurepip' 2>/dev/null; then
  if command -v apt-get >/dev/null; then
    echo "   installing python3-venv (apt)"
    apt-get update -qq && apt-get install -y -qq python3-venv >/dev/null
  else
    fail "python3 venv/ensurepip unavailable and apt-get missing"
  fi
fi
ok "venv + ensurepip available"

log "service user and directories"
if ! getent group "$SVC_USER" >/dev/null; then groupadd --system "$SVC_USER"; fi
if ! id "$SVC_USER" >/dev/null 2>&1; then
  useradd --system --gid "$SVC_USER" --home-dir "$DATA_DIR" --no-create-home --shell /usr/sbin/nologin "$SVC_USER"
fi
for g in video render; do
  if getent group "$g" >/dev/null; then usermod -aG "$g" "$SVC_USER"; echo "   $SVC_USER in group $g (GPU device access)"; fi
done
install -d -m 0700 -o "$SVC_USER" -g "$SVC_USER" "$DATA_DIR"
install -d -m 0755 "$PREFIX"
[[ "$(stat -c '%a %U' "$DATA_DIR")" == "700 $SVC_USER" ]] || fail "$DATA_DIR is not 0700 $SVC_USER"
ok "$DATA_DIR is 0700 owned by $SVC_USER"

log "agent package -> $VENV"
if [[ ! -x "$VENV/bin/python" ]]; then python3 -m venv "$VENV"; fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
# The agent is stdlib-only: --no-deps proves nothing else is pulled in.
"$VENV/bin/python" -m pip install --quiet --no-deps --upgrade "$SOURCE"
"$VENV/bin/python" -c 'import convoy_agent, sys; print("   convoy_agent", convoy_agent.__version__, "on python", sys.version.split()[0])'
"$VENV/bin/convoy-agent" --help >/dev/null || fail "convoy-agent CLI does not run"
DEPS="$("$VENV/bin/python" -m pip freeze --exclude-editable 2>/dev/null | grep -v -i '^convoy[-_]agent' || true)"
[[ -z "$DEPS" ]] && ok "no third-party packages in the venv (stdlib-only agent)" || echo "   [warning] extra packages in venv: $DEPS"
ok "convoy-agent CLI runs"
[[ -r "$GUIDE_SRC" ]] && install -m 0644 "$GUIDE_SRC" "$PREFIX/JETSON_GUIDE.md"

log "systemd unit -> $UNIT_DST"
install -m 0644 "$UNIT_SRC" "$UNIT_DST"
systemctl daemon-reload
if command -v systemd-analyze >/dev/null; then
  systemd-analyze verify "$UNIT_DST" && ok "systemd-analyze verify passed" || echo "   [warning] systemd-analyze verify reported issues above"
fi
if [[ "$ENABLE" -eq 1 ]]; then systemctl enable convoy-agent >/dev/null 2>&1; ok "unit enabled (starts at boot once enrolled)"; fi
grep -q '^NoNewPrivileges=yes' "$UNIT_DST" && grep -q '^ProtectSystem=strict' "$UNIT_DST" && grep -q '^PrivateTmp=yes' "$UNIT_DST" \
  && ok "unit hardening present: NoNewPrivileges, ProtectSystem=strict (+ReadWritePaths=$DATA_DIR), PrivateTmp"

log "L4T / CUDA detection (what the agent will report)"
if [[ -r /etc/nv_tegra_release ]]; then echo "   $(head -n1 /etc/nv_tegra_release)"; else echo "   [warning] /etc/nv_tegra_release missing: not an L4T host (the agent reports l4t_release=null)"; fi
if [[ -r /usr/local/cuda/version.json ]]; then
  echo "   CUDA $(python3 -c 'import json;print(json.load(open("/usr/local/cuda/version.json"))["cuda"]["version"])' 2>/dev/null || echo unknown)"
else
  echo "   [warning] /usr/local/cuda/version.json missing: the agent reports cuda_version=null"
fi
command -v tegrastats >/dev/null && echo "   tegrastats: $(command -v tegrastats)" || echo "   [note] tegrastats not on PATH; power/thermal telemetry will be null"

log "done"
if [[ -s "$DATA_DIR/credential" ]]; then
  echo "   a credential already exists in $DATA_DIR; restart the service to pick up the new package:"
  echo "     sudo systemctl restart convoy-agent && systemctl status convoy-agent"
else
  echo "   not enrolled yet. Create an enrollment token in the Convoy UI (Fleet -> Enroll device), then:"
  echo "     sudo -u $SVC_USER $VENV/bin/convoy-agent enroll --server https://<host> --token <token> --name <device-name>"
  echo "     sudo systemctl start convoy-agent && journalctl -u convoy-agent -f"
  echo "   (the unit has ConditionPathExists=$DATA_DIR/credential and does nothing before enrollment)"
fi
