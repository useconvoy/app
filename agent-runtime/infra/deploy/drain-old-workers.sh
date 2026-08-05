#!/usr/bin/env bash
#
# drain-old-workers.sh — retire worker services whose build ids no longer
# have reachable runs (the drain-old half of the versioned deploy pattern).
#
# For every ECS service convoy-<stack>-temporal-worker-<build-id> except the
# task queue's current default build id, ask Temporal whether the build id is
# still reachable by any open or retained workflow. Unreachable build ids get
# their service scaled to zero (and deleted with --delete). Reachable ones
# are left running — a deploy on day 3 of a 5-day run must not break replay.
#
# Safe to run on a schedule (e.g. hourly from CI) — it only ever scales down
# builds Temporal reports unreachable, and never touches the current default.
#
# Usage:
#   STACK_NAME=acme-prod AWS_REGION=us-east-1 \
#   TEMPORAL_ADDRESS=... TEMPORAL_NAMESPACE=... \
#   TEMPORAL_TLS_CERT=... TEMPORAL_TLS_KEY=... \
#     ./drain-old-workers.sh [--task-queue agent-runtime] [--delete] [--dry-run]
#
#   --task-queue  Task queue to check reachability against (default: agent-runtime).
#   --delete      Also delete services after scaling them to zero.
#   --dry-run     Report what would happen without changing anything.
#
# Requires: aws CLI v2, jq, temporal CLI.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

DELETE=0 DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --task-queue) TASK_QUEUE="$2"; shift 2 ;;
    --delete) DELETE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) grep '^#' "$0" | cut -c3-; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

need_cmd aws; need_cmd jq
require_stack_env

# Current default build id — never a drain candidate.
DEFAULT_BUILD_ID="$(temporal_cli task-queue get-build-ids \
  --task-queue "${TASK_QUEUE}" --output json |
  jq -r '[.[] | select(.defaultForQueue == true)] | first | .buildIds | last // empty')"
[[ -n "${DEFAULT_BUILD_ID}" ]] ||
  log "warning: no default build id on '${TASK_QUEUE}' (queue unversioned?); all services are candidates"

log "task queue '${TASK_QUEUE}' default build id: ${DEFAULT_BUILD_ID:-<none>}"

mapfile -t WORKER_SERVICES < <(aws ecs list-services --cluster "${CLUSTER_NAME}" --output json |
  jq -r '.serviceArns[] | split("/") | last' |
  grep -F "${WORKER_FAMILY}-" || true)

[[ ${#WORKER_SERVICES[@]} -gt 0 ]] || { log "no worker services found; nothing to do"; exit 0; }

for service in "${WORKER_SERVICES[@]}"; do
  build_id="${service#"${WORKER_FAMILY}"-}"

  if [[ -n "${DEFAULT_BUILD_ID}" && "${build_id}" == "${DEFAULT_BUILD_ID}" ]]; then
    log "KEEP  ${service} (current default build)"
    continue
  fi

  reachability="$(temporal_cli task-queue get-build-id-reachability \
    --task-queue "${TASK_QUEUE}" --build-id "${build_id}" --output json |
    jq -r '[.. | strings | select(. == "NewWorkflows" or . == "ExistingWorkflows"
            or . == "OpenWorkflows" or . == "ClosedWorkflows")] | length')"

  if [[ "${reachability}" -gt 0 ]]; then
    log "KEEP  ${service} (build ${build_id} still reachable by runs)"
    continue
  fi

  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "WOULD RETIRE ${service} (build ${build_id} unreachable)"
    continue
  fi

  log "RETIRE ${service}: scaling to 0"
  aws ecs update-service --cluster "${CLUSTER_NAME}" \
    --service "${service}" --desired-count 0 >/dev/null
  if [[ "${DELETE}" -eq 1 ]]; then
    wait_service_stable "${service}"
    log "RETIRE ${service}: deleting"
    aws ecs delete-service --cluster "${CLUSTER_NAME}" \
      --service "${service}" >/dev/null
  fi
done

log "drain pass complete"
