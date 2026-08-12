#!/usr/bin/env bash
#
# common.sh — shared helpers for the Convoy console deploy pipeline.
# Sourced by the sibling scripts; not runnable on its own.
#
# Required environment (or flags on the calling scripts):
#   STACK_NAME   Console short name, e.g. "demo" (resource prefix convoy-console-<name>).
#   AWS_REGION   Console region (falls back to AWS_DEFAULT_REGION / aws configure).
#
# Optional environment:
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
  printf '%s' "${ECR_REPO_URL:-$(ecr_registry)/${NAME_PREFIX}/${ECR_REPO_NAME:-website}}"
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
    --repository-name "${NAME_PREFIX}/${ECR_REPO_NAME:-website}" \
    --image-ids "imageTag=$1" >/dev/null 2>&1
}

# The ECS helpers that used to live here — task-definition cloning, service
# stability waits, and private-subnet discovery — went with the ECS stack. The
# demo runs docker compose on one instance, so a release is a pull and a
# restart over SSH rather than a task-definition revision.
