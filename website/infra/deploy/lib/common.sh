#!/usr/bin/env bash
#
# common.sh — shared helpers for the Convoy console deploy pipeline.
# Sourced by the sibling scripts; not runnable on its own.
#
# Required environment (or flags on the calling scripts):
#   STACK_NAME   Console short name, e.g. "prod" (resource prefix convoy-console-<name>).
#   AWS_REGION   Console region (falls back to AWS_DEFAULT_REGION / aws configure).
#
# Optional environment:
#   CLUSTER_NAME   Override the derived ECS cluster name.
#   ECR_REPO_URL   Override the derived ECR repository URL.
#
# Dependencies: aws CLI v2, jq. docker for build-and-push.sh.

set -euo pipefail

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

require_stack_env() {
  [[ -n "${STACK_NAME:-}" ]] || die "STACK_NAME is not set (e.g. STACK_NAME=prod)"
  AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || true)}}"
  [[ -n "${AWS_REGION:-}" ]] || die "AWS_REGION is not set"
  export AWS_REGION
  NAME_PREFIX="convoy-console-${STACK_NAME}"
  CLUSTER_NAME="${CLUSTER_NAME:-${NAME_PREFIX}}"
}

account_id() {
  aws sts get-caller-identity --query Account --output text
}

ecr_registry() {
  printf '%s.dkr.ecr.%s.amazonaws.com' "$(account_id)" "${AWS_REGION}"
}

# One repository for the whole console: web, notifier, and migrate are three
# commands over the same image.
ecr_repo_url() {
  printf '%s' "${ECR_REPO_URL:-$(ecr_registry)/${NAME_PREFIX}/website}"
}

ecr_login() {
  aws ecr get-authorization-token --output text \
    --query 'authorizationData[0].authorizationToken' |
    base64 -d | cut -d: -f2 |
    docker login --username AWS --password-stdin "$(ecr_registry)" >/dev/null
  log "logged in to $(ecr_registry)"
}

# The repository rejects a re-pushed tag, so catch it here with an explanation
# instead of letting the push fail with a registry error.
ecr_tag_exists() { # $1 = tag
  aws ecr describe-images \
    --repository-name "${NAME_PREFIX}/website" \
    --image-ids "imageTag=$1" >/dev/null 2>&1
}

# Clone the latest ACTIVE revision of a task-definition family, override the
# first container's image, register the clone, and print the new revision ARN.
#   clone_task_definition FAMILY IMAGE
clone_task_definition() {
  local family="$1" image="$2"

  local current
  current="$(aws ecs describe-task-definition \
    --task-definition "${family}" \
    --query 'taskDefinition' --output json)" ||
    die "task-definition family not found: ${family} (has the console been stamped?)"

  local next
  next="$(jq --arg image "${image}" '
      del(.taskDefinitionArn, .revision, .status, .requiresAttributes,
          .compatibilities, .registeredAt, .registeredBy, .deregisteredAt)
      | .containerDefinitions[0].image = $image
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

# Private subnets and the no-ingress task security group, discovered from tags
# so the scripts need nothing but STACK_NAME and a region. Sets SUBNET_IDS and
# TASK_SECURITY_GROUP_ID.
resolve_task_network() {
  SUBNET_IDS="${SUBNET_IDS:-$(aws ec2 describe-subnets \
    --filters "Name=tag:Name,Values=${NAME_PREFIX}-private-*" \
    --query 'Subnets[].SubnetId' --output text | tr '\t' ',')}"
  [[ -n "${SUBNET_IDS}" ]] ||
    die "no private subnets tagged ${NAME_PREFIX}-private-* (has the console been stamped in ${AWS_REGION}?)"

  TASK_SECURITY_GROUP_ID="${TASK_SECURITY_GROUP_ID:-$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=${NAME_PREFIX}-tasks" \
    --query 'SecurityGroups[0].GroupId' --output text)}"
  [[ -n "${TASK_SECURITY_GROUP_ID}" && "${TASK_SECURITY_GROUP_ID}" != "None" ]] ||
    die "security group ${NAME_PREFIX}-tasks not found"
}
