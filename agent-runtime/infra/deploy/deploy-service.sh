#!/usr/bin/env bash
#
# deploy-service.sh — rolling deploy for the STATELESS services
# (control-plane, litellm). NOT for Temporal workers: workers are
# build-id-versioned and must go through deploy-workers.sh so mid-flight runs
# keep replaying on the build that started them (DESIGN §8.3).
#
# Usage:
#   STACK_NAME=acme-prod AWS_REGION=us-east-1 \
#     ./deploy-service.sh --service control-plane --tag <git-sha> [--image <uri>]
#
#   --service  control-plane | litellm.
#   --tag      Image tag previously pushed by build-and-push.sh.
#   --image    Full image URI override (skips the ECR default of
#              <registry>/convoy-<stack>/<service>:<tag>).
#
# Registers a new task-definition revision with the new image, updates the
# ECS service (circuit breaker rolls back on failure), waits for stability.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

SERVICE="" TAG="" IMAGE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service) SERVICE="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    -h|--help) grep '^#' "$0" | cut -c3-; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

case "${SERVICE}" in
  control-plane|litellm) ;;
  temporal-worker) die "use deploy-workers.sh for workers (build-id versioning)" ;;
  *) die "--service must be control-plane or litellm" ;;
esac
[[ -n "${TAG}" || -n "${IMAGE}" ]] || die "--tag or --image is required"

need_cmd aws; need_cmd jq
require_stack_env

FAMILY="${NAME_PREFIX}-${SERVICE}"
ECS_SERVICE="${NAME_PREFIX}-${SERVICE}"
IMAGE="${IMAGE:-$(ecr_repo_url "${SERVICE}"):${TAG}}"

log "registering new revision of ${FAMILY} with image ${IMAGE}"
NEW_TASKDEF_ARN="$(clone_task_definition "${FAMILY}" "${IMAGE}")"
log "registered ${NEW_TASKDEF_ARN}"

aws ecs update-service \
  --cluster "${CLUSTER_NAME}" \
  --service "${ECS_SERVICE}" \
  --task-definition "${NEW_TASKDEF_ARN}" >/dev/null
wait_service_stable "${ECS_SERVICE}"

log "deployed ${SERVICE} -> ${IMAGE}"
