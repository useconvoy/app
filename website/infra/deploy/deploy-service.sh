#!/usr/bin/env bash
#
# deploy-service.sh — rolling deploy of the console services to a new image.
#
# Both services are stateless: the web tasks hold nothing between requests,
# and the notifier's cursor lives in the database, so a replaced task resumes
# where the old one stopped. Neither needs draining.
#
# Usage:
#   STACK_NAME=prod AWS_REGION=us-east-1 \
#     ./deploy-service.sh --tag <git-sha> [--service web|notifier|all] [--image <uri>]
#
#   --tag      Image tag previously pushed by build-and-push.sh.
#   --service  web | notifier | all (default: all — the two run one image and
#              drifting them apart means the notifier writes notifications for
#              a UI that no longer matches).
#   --image    Full image URI override (skips the ECR default of
#              <registry>/convoy-console-<stack>/website:<tag>).
#
# For each service: register a task-definition revision with the new image,
# update the service (the circuit breaker rolls back a failing rollout), wait
# for stability. Migrations are a separate step — run run-migrations.sh first
# when a release adds them.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

SERVICE="all" TAG="" IMAGE=""

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
  web|notifier|all) ;;
  *) die "--service must be web, notifier, or all" ;;
esac
[[ -n "${TAG}" || -n "${IMAGE}" ]] || die "--tag or --image is required"

need_cmd aws; need_cmd jq
require_stack_env

IMAGE="${IMAGE:-$(ecr_repo_url):${TAG}}"

if [[ "${SERVICE}" == "all" ]]; then
  TARGETS=(web notifier)
else
  TARGETS=("${SERVICE}")
fi

for target in "${TARGETS[@]}"; do
  family="${NAME_PREFIX}-${target}"
  ecs_service="${NAME_PREFIX}-${target}"

  log "registering new revision of ${family} with image ${IMAGE}"
  new_taskdef_arn="$(clone_task_definition "${family}" "${IMAGE}")"
  log "registered ${new_taskdef_arn}"

  aws ecs update-service \
    --cluster "${CLUSTER_NAME}" \
    --service "${ecs_service}" \
    --task-definition "${new_taskdef_arn}" >/dev/null
  wait_service_stable "${ecs_service}"

  log "deployed ${target} -> ${IMAGE}"
done

# The migrate task definition is not attached to a service, so nothing would
# update it on its own. Keeping it on the deployed image means the next
# migration run uses the code that is actually live.
log "registering new revision of ${NAME_PREFIX}-migrate with image ${IMAGE}"
clone_task_definition "${NAME_PREFIX}-migrate" "${IMAGE}" >/dev/null
log "migrate task definition now points at ${IMAGE}"
