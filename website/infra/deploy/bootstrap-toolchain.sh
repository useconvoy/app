#!/usr/bin/env bash
# Install the deploy prerequisites into an environment that does not already
# have them — a fresh CI sandbox or container, where the runbook's "aws CLI v2,
# Terraform >= 1.10" line is an unmet assumption rather than a given.
#
# Idempotent: every step is skipped when the tool is already present and new
# enough, so re-running costs nothing.
#
# Terraform's floor here is 1.10, not the module's 1.6: the S3 backend uses
# use_lockfile for native state locking, which older versions reject as an
# unknown argument.
#
# The provider mirror exists for networks that block registry.terraform.io.
# `terraform init` reaches the registry by default and fails closed there, so
# providers are staged from releases.hashicorp.com instead and init is pointed
# at the mirror ROOT — the directory holding registry.terraform.io/, not one
# level down:
#
#   terraform init -plugin-dir="${CONVOY_TF_MIRROR}"
#
# Versions come from the committed .terraform.lock.hcl; change them there, not
# here, so the mirror and the lock cannot disagree.
set -euo pipefail

TERRAFORM_VERSION="${TERRAFORM_VERSION:-1.15.8}"
AWS_PROVIDER_VERSION="${AWS_PROVIDER_VERSION:-6.57.1}"
RANDOM_PROVIDER_VERSION="${RANDOM_PROVIDER_VERSION:-3.9.0}"
MIRROR_DIR="${CONVOY_TF_MIRROR:-${HOME}/.convoy-tf-mirror}"

log() { printf '==> %s\n' "$*"; }

workdir="$(mktemp -d)"
trap 'rm -rf "${workdir}"' EXIT

# --- aws CLI v2 ------------------------------------------------------------
if command -v aws >/dev/null 2>&1 && aws --version 2>&1 | grep -q 'aws-cli/2'; then
  log "aws CLI present: $(aws --version 2>&1)"
else
  log "installing aws CLI v2"
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" \
    -o "${workdir}/awscliv2.zip"
  unzip -q "${workdir}/awscliv2.zip" -d "${workdir}"
  "${workdir}/aws/install" --bin-dir /usr/local/bin \
    --install-dir /usr/local/aws-cli --update >/dev/null
  log "installed $(aws --version 2>&1)"
fi

# --- terraform -------------------------------------------------------------
have_tf=""
if command -v terraform >/dev/null 2>&1; then
  have_tf="$(terraform version -json 2>/dev/null |
    sed -n 's/.*"terraform_version": *"\([^"]*\)".*/\1/p' | head -1)"
fi
if [ "${have_tf}" = "${TERRAFORM_VERSION}" ]; then
  log "terraform ${TERRAFORM_VERSION} present"
else
  log "installing terraform ${TERRAFORM_VERSION} (found: ${have_tf:-none})"
  curl -fsSL \
    "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip" \
    -o "${workdir}/terraform.zip"
  unzip -q -o "${workdir}/terraform.zip" -d /usr/local/bin
  chmod +x /usr/local/bin/terraform
  log "installed $(terraform version | head -1)"
fi

# --- provider mirror -------------------------------------------------------
stage_provider() {
  local name="$1" version="$2"
  local dest="${MIRROR_DIR}/registry.terraform.io/hashicorp/${name}/${version}/linux_amd64"
  if compgen -G "${dest}/terraform-provider-${name}*" >/dev/null 2>&1; then
    log "provider ${name} ${version} already staged"
    return
  fi
  log "staging provider ${name} ${version}"
  mkdir -p "${dest}"
  curl -fsSL \
    "https://releases.hashicorp.com/terraform-provider-${name}/${version}/terraform-provider-${name}_${version}_linux_amd64.zip" \
    -o "${workdir}/${name}.zip"
  unzip -q -o "${workdir}/${name}.zip" -d "${dest}"
}

stage_provider aws "${AWS_PROVIDER_VERSION}"
stage_provider random "${RANDOM_PROVIDER_VERSION}"

log "toolchain ready"
log "provider mirror: ${MIRROR_DIR}"
log "  terraform init -plugin-dir=\"${MIRROR_DIR}\""
