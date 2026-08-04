#!/usr/bin/env bash
#
# common.sh — shared helpers for the Convoy stack deploy pipeline.
# Sourced by the sibling scripts; not runnable on its own.
#
# Required environment (or flags on the calling scripts):
#   STACK_NAME   Stack short name, e.g. "acme-prod" (resource prefix convoy-<name>).
#   AWS_REGION   Stack region (falls back to AWS_DEFAULT_REGION / aws configure).
#
# Optional environment:
#   TEMPORAL_ADDRESS / TEMPORAL_NAMESPACE / TEMPORAL_TLS_CERT / TEMPORAL_TLS_KEY
#     Passed through to the `temporal` CLI for worker-versioning cutover
#     (deploy-workers.sh, drain-old-workers.sh).
#
# Dependencies: aws CLI v2, jq. docker for build-and-push.sh; temporal CLI
# for the worker-versioning steps.

set -euo pipefail

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

require_stack_env() {
  [[ -n "${STACK_NAME:-}" ]] || die "STACK_NAME is not set (e.g. STACK_NAME=acme-prod)"
  AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || true)}}"
  [[ -n "${AWS_REGION:-}" ]] || die "AWS_REGION is not set"
  export AWS_REGION
  NAME_PREFIX="convoy-${STACK_NAME}"
  CLUSTER_NAME="${CLUSTER_NAME:-${NAME_PREFIX}}"
  WORKER_FAMILY="${WORKER_FAMILY:-${NAME_PREFIX}-temporal-worker}"
  TASK_QUEUE="${TASK_QUEUE:-agent-runtime}"
}

account_id() {
  aws sts get-caller-identity --query Account --output text
}

ecr_registry() {
  printf '%s.dkr.ecr.%s.amazonaws.com' "$(account_id)" "${AWS_REGION}"
}

ecr_repo_url() { # $1 = service (control-plane|temporal-worker|litellm|sandbox)
  printf '%s/%s/%s' "$(ecr_registry)" "${NAME_PREFIX}" "$1"
}

ecr_login() {
  aws ecr get-authorization-token --output text \
    --query 'authorizationData[0].authorizationToken' |
    base64 -d | cut -d: -f2 |
    docker login --username AWS --password-stdin "$(ecr_registry)" >/dev/null
  log "logged in to $(ecr_registry)"
}

# Clone the latest ACTIVE revision of a task-definition family, override the
# first container's image (and optionally one env var), register the clone,
# and print the new revision ARN.
#   clone_task_definition FAMILY IMAGE [ENV_NAME ENV_VALUE]
clone_task_definition() {
  local family="$1" image="$2" env_name="${3:-}" env_value="${4:-}"

  local current
  current="$(aws ecs describe-task-definition \
    --task-definition "${family}" \
    --query 'taskDefinition' --output json)" ||
    die "task-definition family not found: ${family} (has the stack been stamped?)"

  local next
  next="$(jq \
    --arg image "${image}" \
    --arg env_name "${env_name}" \
    --arg env_value "${env_value}" '
      del(.taskDefinitionArn, .revision, .status, .requiresAttributes,
          .compatibilities, .registeredAt, .registeredBy, .deregisteredAt)
      | .containerDefinitions[0].image = $image
      | if $env_name != "" then
          .containerDefinitions[0].environment |=
            (map(select(.name != $env_name)) + [{name: $env_name, value: $env_value}])
        else . end
    ' <<<"${current}")"

  aws ecs register-task-definition \
    --cli-input-json "${next}" \
    --query 'taskDefinition.taskDefinitionArn' --output text
}

wait_service_stable() { # $1 = service name
  log "waiting for ${1} to reach steady state..."
  aws ecs wait services-stable --cluster "${CLUSTER_NAME}" --services "$1"
  log "${1} is stable"
}

# temporal CLI wrapper: injects address/namespace/mTLS from the environment.
temporal_cli() {
  need_cmd temporal
  [[ -n "${TEMPORAL_ADDRESS:-}" ]] || die "TEMPORAL_ADDRESS is not set"
  [[ -n "${TEMPORAL_NAMESPACE:-}" ]] || die "TEMPORAL_NAMESPACE is not set"
  local -a tls_args=()
  [[ -n "${TEMPORAL_TLS_CERT:-}" ]] && tls_args+=(--tls-cert-path "${TEMPORAL_TLS_CERT}")
  [[ -n "${TEMPORAL_TLS_KEY:-}" ]] && tls_args+=(--tls-key-path "${TEMPORAL_TLS_KEY}")
  temporal --address "${TEMPORAL_ADDRESS}" --namespace "${TEMPORAL_NAMESPACE}" \
    "${tls_args[@]}" "$@"
}
