#!/usr/bin/env bash
#
# deploy-workers.sh — build-id-versioned rollout for Temporal workers.
#
# Temporal runs are pinned to the worker build id that started them, so a
# worker deploy must never replace running workers in place. Instead:
#
#   1. Register a new task-definition revision of the worker family with the
#      new image and TEMPORAL_WORKER_BUILD_ID=<build-id>.
#   2. Create a NEW ECS service convoy-<stack>-temporal-worker-<build-id>
#      alongside the old one(s), cloning network config from the newest
#      existing worker service.
#   3. Wait for the new service to reach steady state.
#   4. Cut the task queue's default over to the new build id via the Temporal
#      CLI — new runs land on the new build; pinned runs keep draining on the
#      old services until drain-old-workers.sh retires them.
#
# Usage:
#   STACK_NAME=acme-prod AWS_REGION=us-east-1 \
#   TEMPORAL_ADDRESS=acme-prod.a1b2c.tmprl.cloud:7233 \
#   TEMPORAL_NAMESPACE=acme-prod.a1b2c \
#   TEMPORAL_TLS_CERT=/path/client.pem TEMPORAL_TLS_KEY=/path/client.key \
#     ./deploy-workers.sh --build-id <git-sha> [--image <uri>] \
#       [--desired-count N] [--task-queue agent-runtime] [--skip-temporal-cutover]
#
#   --build-id              Worker build id (convention: git SHA; must match
#                           the image tag pushed by build-and-push.sh).
#   --image                 Full image URI override.
#   --desired-count         Task count for the new service (default: copied
#                           from the newest existing worker service).
#   --task-queue            Task queue to cut over (default: agent-runtime).
#   --skip-temporal-cutover Provision the service but skip step 4 (e.g. to
#                           smoke-test the new build before flipping traffic).
#
# Requires: aws CLI v2, jq, temporal CLI (unless --skip-temporal-cutover).
# Callable from CI; idempotent per build id (re-running updates the same
# service in place).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

BUILD_ID="" IMAGE="" DESIRED_COUNT="" CUTOVER=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build-id) BUILD_ID="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --desired-count) DESIRED_COUNT="$2"; shift 2 ;;
    --task-queue) TASK_QUEUE="$2"; shift 2 ;;
    --skip-temporal-cutover) CUTOVER=0; shift ;;
    -h|--help) grep '^#' "$0" | cut -c3-; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "${BUILD_ID}" ]] || die "--build-id is required (use the git SHA)"
[[ "${BUILD_ID}" =~ ^[a-zA-Z0-9._-]{1,64}$ ]] ||
  die "--build-id must be 1-64 chars of [a-zA-Z0-9._-] (it is embedded in the ECS service name)"

need_cmd aws; need_cmd jq
require_stack_env

IMAGE="${IMAGE:-$(ecr_repo_url temporal-worker):${BUILD_ID}}"
NEW_SERVICE="${WORKER_FAMILY}-${BUILD_ID}"

# --- 1. Register the build's task-definition revision ----------------------

log "registering ${WORKER_FAMILY} revision: image=${IMAGE} build_id=${BUILD_ID}"
NEW_TASKDEF_ARN="$(clone_task_definition "${WORKER_FAMILY}" "${IMAGE}" \
  TEMPORAL_WORKER_BUILD_ID "${BUILD_ID}")"
log "registered ${NEW_TASKDEF_ARN}"

# --- 2. Create (or update) the per-build-id service ------------------------

# Clone network config + desired count from the newest existing worker service.
TEMPLATE_SERVICE_JSON="$(aws ecs list-services --cluster "${CLUSTER_NAME}" --output json |
  jq -r '.serviceArns[]' |
  grep -F "/${WORKER_FAMILY}-" |
  head -10 |
  xargs -r aws ecs describe-services --cluster "${CLUSTER_NAME}" --output json --services |
  jq '[.services[] | select(.status == "ACTIVE")] | sort_by(.createdAt) | last')"
[[ "${TEMPLATE_SERVICE_JSON}" != "null" && -n "${TEMPLATE_SERVICE_JSON}" ]] ||
  die "no existing ${WORKER_FAMILY}-* service found to clone network config from (stamp the stack first)"

NETWORK_CONFIG="$(jq -c '.networkConfiguration' <<<"${TEMPLATE_SERVICE_JSON}")"
DESIRED_COUNT="${DESIRED_COUNT:-$(jq -r '.desiredCount' <<<"${TEMPLATE_SERVICE_JSON}")}"

EXISTING_STATUS="$(aws ecs describe-services --cluster "${CLUSTER_NAME}" \
  --services "${NEW_SERVICE}" --output json |
  jq -r '.services[0].status // "MISSING"')"

if [[ "${EXISTING_STATUS}" == "ACTIVE" ]]; then
  log "service ${NEW_SERVICE} already exists; updating in place"
  aws ecs update-service \
    --cluster "${CLUSTER_NAME}" \
    --service "${NEW_SERVICE}" \
    --task-definition "${NEW_TASKDEF_ARN}" \
    --desired-count "${DESIRED_COUNT}" >/dev/null
else
  log "creating service ${NEW_SERVICE} (desired=${DESIRED_COUNT})"
  aws ecs create-service \
    --cluster "${CLUSTER_NAME}" \
    --service-name "${NEW_SERVICE}" \
    --task-definition "${NEW_TASKDEF_ARN}" \
    --desired-count "${DESIRED_COUNT}" \
    --launch-type FARGATE \
    --network-configuration "${NETWORK_CONFIG}" \
    --deployment-configuration 'deploymentCircuitBreaker={enable=true,rollback=true}' \
    --propagate-tags SERVICE \
    --tags "key=convoy:stack,value=${STACK_NAME}" \
    "key=convoy:build-id,value=${BUILD_ID}" \
    "key=managed-by,value=deploy-pipeline" >/dev/null
fi

# --- 3. Wait for steady state ----------------------------------------------

wait_service_stable "${NEW_SERVICE}"

# --- 4. Cut the task queue default over to the new build -------------------

if [[ "${CUTOVER}" -eq 1 ]]; then
  log "setting default build id for task queue '${TASK_QUEUE}' -> ${BUILD_ID}"
  temporal_cli task-queue update-build-ids add-new-default \
    --task-queue "${TASK_QUEUE}" \
    --build-id "${BUILD_ID}"
  log "cutover complete: new runs start on ${BUILD_ID}; old builds keep draining"
  log "next: run drain-old-workers.sh periodically until old services retire"
else
  log "skipped Temporal cutover (--skip-temporal-cutover); task queue default unchanged"
fi
