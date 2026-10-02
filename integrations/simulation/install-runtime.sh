#!/usr/bin/env bash
# Install an immutable copy of this checkout's committed simulator runtime, without root.
set -euo pipefail
umask 077
if [[ $# != 1 || "$1" != /* ]]; then
  echo "Usage: bash integrations/simulation/install-runtime.sh /absolute/install/directory" >&2
  exit 2
fi
convoy_source_root=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
convoy_revision=$(git -C "$convoy_source_root" rev-parse HEAD)
convoy_install_dir=$1
if [[ -n "$(git -C "$convoy_source_root" status --porcelain -- control-plane integrations/simulation)" ]]; then
  echo "Commit runtime changes before installing; installation uses the exact committed source." >&2
  exit 2
fi
if [[ -e "$convoy_install_dir" ]]; then
  if [[ ! -f "$convoy_install_dir/source-revision" || "$(cat "$convoy_install_dir/source-revision")" != "$convoy_revision" ]]; then
    echo "Choose an empty destination or the same revision's existing installation." >&2
    exit 2
  fi
else
  mkdir -p "$convoy_install_dir/source"
  git -C "$convoy_source_root" archive "$convoy_revision" control-plane integrations/simulation | tar -xf - -C "$convoy_install_dir/source"
  printf '%s\n' "$convoy_revision" > "$convoy_install_dir/source-revision"
fi
if command -v uv >/dev/null 2>&1; then
  convoy_uv=$(command -v uv)
else
  if [[ ! -x "$convoy_install_dir/bootstrap/bin/uv" ]]; then
    python3 -m venv "$convoy_install_dir/bootstrap"
    "$convoy_install_dir/bootstrap/bin/python" -m pip install 'uv==0.9.28'
  fi
  convoy_uv="$convoy_install_dir/bootstrap/bin/uv"
fi
UV_PROJECT_ENVIRONMENT="$convoy_install_dir/runtime" "$convoy_uv" sync --python 3.11 --frozen --no-dev --extra runtime --project "$convoy_install_dir/source/integrations/simulation"
printf '\nInstalled Convoy simulator revision %s. Activate it before using the project connection commands:\n' "$convoy_revision"
printf 'source %q\n' "$convoy_install_dir/runtime/bin/activate"
