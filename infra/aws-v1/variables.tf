variable "name" {
  type    = string
  default = "convoy-v1-staging"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,23}$", var.name))
    error_message = "Use 3–24 lowercase letters/digits/hyphens, starting with a letter."
  }
}
variable "account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "Supply the approved AWS account ID."
  }
}
variable "region" { type = string }
variable "availability_zones" {
  type = list(string)
  validation {
    condition     = length(var.availability_zones) == 2 && length(toset(var.availability_zones)) == 2
    error_message = "Supply two distinct AZs in the selected region."
  }
}
variable "vpc_cidr" {
  type    = string
  default = "10.74.0.0/16"
}
variable "zone_id" { type = string }
variable "domains" {
  type = object({ web = string, api = string, inference = string })
  validation {
    condition     = length(toset(values(var.domains))) == 3 && alltrue([for domain in values(var.domains) : can(regex("^[a-z0-9][a-z0-9.-]+\\.[a-z]{2,}$", domain))])
    error_message = "Use three distinct DNS names in the supplied public Route53 zone."
  }
}
variable "certificate_arns" {
  description = "Existing ISSUED ACM certs in this region; management covers web+api, inference covers inference."
  type        = object({ management = string, inference = string })
}
variable "images" {
  description = "Same-account, same-region private ECR digests matching cpu_architecture. API/reference use the infra/aws-v1 runtime wrappers."
  type        = object({ api = string, web = string, inference = string })
  validation {
    condition     = alltrue([for image in values(var.images) : can(regex("^[0-9]{12}\\.dkr\\.ecr\\.[a-z0-9-]+\\.amazonaws\\.com/[a-z0-9_./-]+@sha256:[a-f0-9]{64}$", image))])
    error_message = "Every image must be a private ECR reference pinned to a SHA256 digest."
  }
}
variable "services_enabled" {
  description = "Keep false until secret versions, DB roles and the explicit migration task have succeeded."
  type        = bool
  default     = false
}
variable "enable_evaluations" {
  description = "Requires an API image containing convoy_server.evaluation_worker and migration 0002_evaluations."
  type        = bool
  default     = true
}
variable "postgres_version" {
  description = "Confirm this PostgreSQL 17 minor is offered in the selected region before planning against AWS."
  type        = string
  validation {
    condition     = can(regex("^17\\.[0-9]+$", var.postgres_version))
    error_message = "This configuration requires a qualified PostgreSQL 17 minor."
  }
}
variable "budget_usd" {
  description = "Explicit approved account-wide monthly warning threshold, not a spending cap."
  type        = number
  validation {
    condition     = var.budget_usd > 0
    error_message = "Supply the approved warning threshold before applying."
  }
}
variable "budget_email" { type = string }

variable "cpu_architecture" {
  type = string
  validation {
    condition     = contains(["ARM64", "X86_64"], var.cpu_architecture)
    error_message = "Choose the architecture actually qualified for all three image digests."
  }
}

variable "final_snapshot_identifier" {
  description = "Unique retained snapshot name for this installation teardown; choose a fresh value after restore/recreation."
  type        = string
}
