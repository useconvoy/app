#!/usr/bin/env bash
#
# build-and-push.sh — build one service image and push it to the stack's ECR.
#
# Usage:
#   STACK_NAME=acme-prod AWS_REGION=us-east-1 \
#     ./build-and-push.sh --service temporal-worker --tag <git-sha> \
#       [--context <dir>] [--dockerfile <path>] [--platform linux/amd64]
#
#   --service     One of: control-plane | temporal-worker | litellm | sandbox.
#   --tag         Image tag; use the git SHA (it doubles as the worker build id).
#   --context     Docker build context (default: monorepo root, two levels up).
#   --dockerfile  Dockerfile path (default: <context>/agent-runtime/docker/<service>.Dockerfile).
#   --platform    Target platform (default: linux/amd64 — must match the
#                 module's cpu_architecture, X86_64 by default).
#
# Callable from CI: exits non-zero on any failure; all output on stderr except
# the final pushed image URI on stdout.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

SERVICE="" TAG="" CONTEXT="" DOCKERFILE="" PLATFORM="linux/amd64"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service) SERVICE="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --context) CONTEXT="$2"; shift 2 ;;
    --dockerfile) DOCKERFILE="$2"; shift 2 ;;
    --platform) PLATFORM="$2"; shift 2 ;;
    -h|--help) grep '^#' "$0" | cut -c3-; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

case "${SERVICE}" in
  control-plane|temporal-worker|litellm|sandbox) ;;
  *) die "--service must be one of: control-plane, temporal-worker, litellm, sandbox" ;;
esac
[[ -n "${TAG}" ]] || die "--tag is required (use the git SHA)"

need_cmd aws; need_cmd jq; need_cmd docker
require_stack_env

CONTEXT="${CONTEXT:-$(cd "${SCRIPT_DIR}/../../.." && pwd)}"
DOCKERFILE="${DOCKERFILE:-${CONTEXT}/agent-runtime/docker/${SERVICE}.Dockerfile}"
[[ -f "${DOCKERFILE}" ]] || die "Dockerfile not found: ${DOCKERFILE} (pass --dockerfile)"

IMAGE="$(ecr_repo_url "${SERVICE}"):${TAG}"

log "building ${IMAGE} (${PLATFORM}) from ${DOCKERFILE}"
docker build --platform "${PLATFORM}" -f "${DOCKERFILE}" -t "${IMAGE}" "${CONTEXT}"

ecr_login
log "pushing ${IMAGE}"
docker push "${IMAGE}" >&2

echo "${IMAGE}"
