#!/usr/bin/env bash
#
# build-and-push.sh — build the console image and push it to the console's ECR.
#
# One image serves all three entry points (web, notifier, migrate), so there
# is one build per release and nothing to keep in sync.
#
# Usage:
#   STACK_NAME=prod AWS_REGION=us-east-1 \
#     ./build-and-push.sh --tag <git-sha> \
#       [--context <dir>] [--dockerfile <path>] [--platform linux/amd64]
#
#   --tag         Image tag; use the git SHA.
#   --context     Docker build context (default: the website/ directory).
#   --dockerfile  Dockerfile path (default: <context>/Dockerfile).
#   --platform    Target platform (default: linux/amd64 — must match the
#                 module's cpu_architecture, X86_64 by default).
#
# The repository has immutable tags: a tag that already exists cannot be
# overwritten, and this script stops before the build rather than after it.
#
# Callable from CI: exits non-zero on any failure; all output on stderr except
# the final pushed image URI on stdout.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

TAG="" CONTEXT="" DOCKERFILE="" PLATFORM="linux/amd64" REPO="website"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag) TAG="$2"; shift 2 ;;
    --context) CONTEXT="$2"; shift 2 ;;
    --dockerfile) DOCKERFILE="$2"; shift 2 ;;
    --platform) PLATFORM="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    -h|--help) grep '^#' "$0" | cut -c3-; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "${TAG}" ]] || die "--tag is required (use the git SHA)"
[[ "${TAG}" =~ ^[a-zA-Z0-9._-]{1,128}$ ]] ||
  die "--tag must be 1-128 chars of [a-zA-Z0-9._-]"

need_cmd aws; need_cmd jq; need_cmd docker
require_stack_env
export ECR_REPO_NAME="${REPO}"

CONTEXT="${CONTEXT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
DOCKERFILE="${DOCKERFILE:-${CONTEXT}/Dockerfile}"
[[ -f "${DOCKERFILE}" ]] || die "Dockerfile not found: ${DOCKERFILE} (pass --dockerfile)"

if ecr_tag_exists "${TAG}"; then
  die "tag ${TAG} already exists in ${NAME_PREFIX}/${REPO} and tags are immutable; pick a new tag"
fi

IMAGE="$(ecr_repo_url):${TAG}"

log "building ${IMAGE} (${PLATFORM}) from ${DOCKERFILE}"
docker build --platform "${PLATFORM}" -f "${DOCKERFILE}" -t "${IMAGE}" "${CONTEXT}"

ecr_login
log "pushing ${IMAGE}"
docker push "${IMAGE}" >&2

echo "${IMAGE}"
