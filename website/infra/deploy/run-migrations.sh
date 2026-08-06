#!/usr/bin/env bash
#
# run-migrations.sh — apply the console's schema from inside the VPC.
#
# The database has no public address and its security group only admits the
# console's own task security groups, so migrations run as a one-off Fargate
# task on the private subnets rather than from an operator's machine. Nothing
# has to be opened, and no human ever holds the master credentials: the task
# definition injects them from Secrets Manager.
#
# Two steps, in order:
#   1. `npm run db:migrate` — applies db/migrations in filename order.
#   2. infra/db/sync-app-role.mjs — points convoy_website_app at the password
#      Terraform generated. The migrations create that role with a fixed
#      development password because a laptop stack needs one; a deployed
#      console must not run on it. Both the admin DSN and the generated
#      password are already injected into this task definition, so the value
#      is never an argument to anything.
#
# Usage:
#   STACK_NAME=prod AWS_REGION=us-east-1 \
#     ./run-migrations.sh [--tag <git-sha>] [--skip-app-role-sync] [--timeout 1800]
#
#   --tag                  Run the migration on this image instead of whatever
#                          revision the task-definition family currently
#                          points at. Use it to migrate ahead of a deploy.
#   --skip-app-role-sync   Run step 1 only.
#   --timeout              Seconds to wait for a task before giving up
#                          (default 1800).
#
# Streams each task's log group to stderr and exits with the container's exit
# code, so a failed migration fails the pipeline.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

TAG="" SYNC_APP_ROLE=1 TIMEOUT=1800

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag) TAG="$2"; shift 2 ;;
    --skip-app-role-sync) SYNC_APP_ROLE=0; shift ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    -h|--help) grep '^#' "$0" | cut -c3-; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

need_cmd aws; need_cmd jq
require_stack_env
resolve_task_network

FAMILY="${NAME_PREFIX}-migrate"
LOG_GROUP="/convoy/console/${STACK_NAME}/migrate"
CONTAINER="migrate"

TASK_DEFINITION="${FAMILY}"
if [[ -n "${TAG}" ]]; then
  log "registering a migrate revision on tag ${TAG}"
  TASK_DEFINITION="$(clone_task_definition "${FAMILY}" "$(ecr_repo_url):${TAG}")"
fi

# Run one command on the migrate task definition, follow its log stream, and
# return the container's exit code.
#   run_one_off DESCRIPTION COMMAND_JSON_ARRAY
run_one_off() {
  local description="$1" command_json="$2"

  local overrides
  overrides="$(jq -nc \
    --arg name "${CONTAINER}" \
    --argjson command "${command_json}" \
    '{containerOverrides: [{name: $name, command: $command}]}')"

  log "running ${description}"
  local task_arn
  task_arn="$(aws ecs run-task \
    --cluster "${CLUSTER_NAME}" \
    --task-definition "${TASK_DEFINITION}" \
    --launch-type FARGATE \
    --count 1 \
    --overrides "${overrides}" \
    --network-configuration "awsvpcConfiguration={subnets=[${SUBNET_IDS}],securityGroups=[${TASK_SECURITY_GROUP_ID}],assignPublicIp=DISABLED}" \
    --query 'tasks[0].taskArn' --output text)"
  [[ -n "${task_arn}" && "${task_arn}" != "None" ]] || die "RunTask returned no task (check the cluster and task definition)"

  local task_id="${task_arn##*/}"
  local log_stream="${CONTAINER}/${CONTAINER}/${task_id}"
  log "task ${task_id} started; logs at ${LOG_GROUP}:${log_stream}"

  local next_token="" status="" exit_code="" waited=0
  while :; do
    next_token="$(tail_logs "${log_stream}" "${next_token}")"

    status="$(aws ecs describe-tasks --cluster "${CLUSTER_NAME}" --tasks "${task_arn}" \
      --query 'tasks[0].lastStatus' --output text)"
    if [[ "${status}" == "STOPPED" ]]; then
      # The last writes usually land after the container exits.
      sleep 3
      tail_logs "${log_stream}" "${next_token}" >/dev/null
      break
    fi

    (( waited += 5 ))
    [[ "${waited}" -lt "${TIMEOUT}" ]] || die "${description} did not finish within ${TIMEOUT}s (task ${task_id} still ${status})"
    sleep 5
  done

  exit_code="$(aws ecs describe-tasks --cluster "${CLUSTER_NAME}" --tasks "${task_arn}" \
    --query "tasks[0].containers[?name=='${CONTAINER}'].exitCode | [0]" --output text)"
  local stopped_reason
  stopped_reason="$(aws ecs describe-tasks --cluster "${CLUSTER_NAME}" --tasks "${task_arn}" \
    --query 'tasks[0].stoppedReason' --output text)"

  if [[ "${exit_code}" != "0" ]]; then
    die "${description} failed (exit ${exit_code}): ${stopped_reason}"
  fi
  log "${description} finished cleanly"
}

# Print any log events after the given token; echo the token to resume from.
# A stream that does not exist yet is not an error — the task may still be
# pulling its image.
tail_logs() { # $1 = stream, $2 = token
  local stream="$1" token="$2"
  local args=(--log-group-name "${LOG_GROUP}" --log-stream-name "${stream}" --start-from-head)
  [[ -n "${token}" ]] && args+=(--next-token "${token}")

  local response
  if ! response="$(aws logs get-log-events "${args[@]}" --output json 2>/dev/null)"; then
    printf '%s' "${token}"
    return 0
  fi

  jq -r '.events[].message' <<<"${response}" >&2
  jq -r '.nextForwardToken' <<<"${response}"
}

run_one_off "schema migrations" '["npm","run","db:migrate"]'

if [[ "${SYNC_APP_ROLE}" -eq 1 ]]; then
  run_one_off "app role password sync" '["node","infra/db/sync-app-role.mjs"]'
else
  log "skipped the app role password sync (--skip-app-role-sync)"
  log "the web and notifier tasks will fail to authenticate until it runs"
fi

log "database is up to date"
