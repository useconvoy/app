#!/usr/bin/env bash
#
# seed-litellm-models.sh — seed the stack's LiteLLM proxy with its model list.
#
# The stamped stack runs LiteLLM with STORE_MODEL_IN_DB=True and no config
# file, so a fresh stack serves zero models until they are seeded. This
# script POSTs each entry of a models file to the proxy admin API; entries
# persist in the stack database, so seeding is a one-time step per stack
# (re-run after adding models — existing model names are skipped).
#
# Model entries reference provider keys as `os.environ/<NAME>` — the proxy
# resolves them from its own container environment, where the module injects
# every secret named in the `model_provider_secrets` variable. No key ever
# appears in the models file or on the wire here.
#
# The proxy is private (Cloud Map DNS inside the stack VPC): run this from
# somewhere that resolves it — a bastion, a one-off ECS task, or an SSM port
# forward — the same pattern as database migrations.
#
# Usage:
#   STACK_NAME=acme-prod AWS_REGION=us-east-1 \
#     ./seed-litellm-models.sh --models-file ./acme-prod-models.json \
#       [--base-url http://litellm.convoy-acme-prod.internal:4000]
#
#   --models-file  JSON array of proxy model definitions; start from
#                  litellm-models.example.json next to this script.
#   --base-url     Proxy base URL (default: the stack's Cloud Map DNS name
#                  on port 4000).
#
# The proxy master key is fetched from the stack's Secrets Manager secret;
# override with LITELLM_MASTER_KEY in the environment to skip the lookup.
#
# Requires: curl, jq; aws CLI v2 unless LITELLM_MASTER_KEY is provided.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

MODELS_FILE="" BASE_URL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --models-file) MODELS_FILE="$2"; shift 2 ;;
    --base-url) BASE_URL="$2"; shift 2 ;;
    -h|--help) grep '^#' "$0" | cut -c3-; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "${MODELS_FILE}" ]] || die "--models-file is required (see litellm-models.example.json)"
[[ -f "${MODELS_FILE}" ]] || die "models file not found: ${MODELS_FILE}"

need_cmd curl; need_cmd jq
require_stack_env

BASE_URL="${BASE_URL:-http://litellm.${NAME_PREFIX}.internal:4000}"

if [[ -z "${LITELLM_MASTER_KEY:-}" ]]; then
  need_cmd aws
  LITELLM_MASTER_KEY="$(aws secretsmanager get-secret-value \
    --secret-id "${NAME_PREFIX}/litellm-master-key" \
    --query 'SecretString' --output text)" ||
    die "could not read secret ${NAME_PREFIX}/litellm-master-key (set LITELLM_MASTER_KEY to skip)"
fi

jq -e 'type == "array"' "${MODELS_FILE}" >/dev/null ||
  die "models file must be a JSON array of {model_name, litellm_params, model_info?} entries"

EXISTING="$(curl -fsS "${BASE_URL}/model/info" \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" |
  jq -r '[.data[].model_name] | unique | .[]')" ||
  die "proxy unreachable at ${BASE_URL} (run from inside the stack VPC)"

SEEDED=0 SKIPPED=0
while IFS= read -r entry; do
  name="$(jq -r '.model_name' <<<"${entry}")"
  [[ "${name}" != "null" && -n "${name}" ]] || die "entry without model_name: ${entry}"
  if grep -Fxq "${name}" <<<"${EXISTING}"; then
    log "model ${name} already present; skipping"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi
  log "seeding model ${name}"
  curl -fsS -X POST "${BASE_URL}/model/new" \
    -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
    -H "Content-Type: application/json" \
    -d "${entry}" >/dev/null ||
    die "seeding ${name} failed"
  SEEDED=$((SEEDED + 1))
done < <(jq -c '.[]' "${MODELS_FILE}")

log "done: ${SEEDED} seeded, ${SKIPPED} already present"
log "verify: curl -H \"Authorization: Bearer \$LITELLM_MASTER_KEY\" ${BASE_URL}/model/info"
