variable "name" {
  type    = string
  default = "convoy-planner-trial"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,23}$", var.name))
    error_message = "Use a unique trial name: 3–24 lowercase letters/digits/hyphens, starting with a letter."
  }
}
variable "account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "Supply the approved AWS account ID."
  }
}
variable "region" {
  description = "Explicit approved commercial AWS region; confirm ARM64 Fargate availability before a real plan."
  type        = string
  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]+$", var.region))
    error_message = "Supply a commercial AWS region name."
  }
}
variable "availability_zones" {
  type = list(string)
  validation {
    condition     = length(var.availability_zones) == 2 && length(toset(var.availability_zones)) == 2 && alltrue([for az in var.availability_zones : can(regex("^${var.region}[a-z]$", az))])
    error_message = "Supply two distinct availability zones in the selected region."
  }
}
variable "vpc_cidr" {
  description = "Dedicated IPv4 /16; select an unused private range for this isolated trial."
  type        = string
  default     = "10.75.0.0/16"
  validation {
    condition     = can(cidrnetmask(var.vpc_cidr)) && endswith(var.vpc_cidr, "/16")
    error_message = "Supply an IPv4 /16 CIDR."
  }
}
variable "client_cidrs" {
  description = "Explicit IPv4 egress CIDRs of the trial client; no global ingress."
  type        = set(string)
  validation {
    condition     = length(var.client_cidrs) > 0 && length(var.client_cidrs) <= 8 && alltrue([for cidr in var.client_cidrs : can(cidrnetmask(cidr)) && try(tonumber(split("/", cidr)[1]) >= 24, false)])
    error_message = "Supply 1–8 IPv4 client CIDRs with prefix length 24–32."
  }
}
variable "domain" {
  description = "User-controlled DNS name covered by the existing certificate; DNS is configured separately."
  type        = string
  validation {
    condition     = length(var.domain) <= 253 && can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", var.domain))
    error_message = "Supply a lowercase DNS name, without scheme, path or wildcard."
  }
}
variable "certificate_arn" {
  description = "Existing ISSUED ACM certificate for domain in the approved account/region; not created or queried here."
  type        = string
  validation {
    condition     = can(regex("^arn:aws:acm:${var.region}:${var.account_id}:certificate/[a-f0-9-]{36}$", var.certificate_arn))
    error_message = "Supply the existing ACM certificate ARN in the approved account and region."
  }
}
variable "image" {
  description = "Requalified Linux ARM64 owned-planner image, including remote placement/release injection, pinned to a private ECR digest."
  type        = string
  validation {
    condition     = can(regex("^${var.account_id}\\.dkr\\.ecr\\.${var.region}\\.amazonaws\\.com/[a-z0-9_./-]+@sha256:[a-f0-9]{64}$", var.image))
    error_message = "Supply a SHA256-pinned private ECR image in the approved account and selected region."
  }
}
variable "planner_verification_secret_arn" {
  description = "Existing Secrets Manager whole-document secret: public planner verification JSON only, encrypted with the AWS-managed Secrets Manager key."
  type        = string
  validation {
    condition     = can(regex("^arn:aws:secretsmanager:${var.region}:${var.account_id}:secret:[A-Za-z0-9/_+=.@-]+-[A-Za-z0-9]{6}$", var.planner_verification_secret_arn))
    error_message = "Supply a complete same-account, same-region public planner secret ARN, without JSON-key/version suffixes."
  }
}
variable "planner_probe_secret_arn" {
  description = "Existing distinct Secrets Manager secret containing the probe token only, encrypted with the AWS-managed Secrets Manager key. No token is a Terraform input."
  type        = string
  validation {
    condition     = can(regex("^arn:aws:secretsmanager:${var.region}:${var.account_id}:secret:[A-Za-z0-9/_+=.@-]+-[A-Za-z0-9]{6}$", var.planner_probe_secret_arn)) && var.planner_probe_secret_arn != var.planner_verification_secret_arn
    error_message = "Supply a distinct complete same-account, same-region probe secret ARN, without JSON-key/version suffixes."
  }
}
variable "release_json" {
  description = "Public immutable paired release JSON, never credentials. Entrypoint strictly validates the 16KiB bound, full contract and canonical SHA256 before native startup. Present in Terraform state/task definition."
  type        = string
  validation {
    condition     = length(var.release_json) <= 16384 && try(jsondecode(var.release_json).profile == "metaworld-smolvla-text-skill-v1" && jsondecode(var.release_json).planner.runtime == "convoy-llamacpp-text-skill-v1" && jsondecode(var.release_json).placement.planner == "development-remote-cpu", false)
    error_message = "Supply the bounded public paired release for the real remote CPU planner; the container performs authoritative validation."
  }
}
variable "release_sha256" {
  description = "Canonical digest produced by convoy_contracts.execution.canonical_digest, verified by the entrypoint. Do not hash pretty-printed JSON or Terraform jsonencode output."
  type        = string
  validation {
    condition     = can(regex("^[a-f0-9]{64}$", var.release_sha256))
    error_message = "Supply the release's lowercase canonical SHA256."
  }
}
variable "planner_enabled" {
  description = "Enable exactly one planner only after reviewing DNS, image, release, authority and spending inputs. False still leaves billable ALB infrastructure if applied."
  type        = bool
  default     = false
}
