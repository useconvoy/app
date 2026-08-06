# Root inputs for the Lightsail demo console.
#
# This stack exists to run the console end to end for roughly the price of a
# sandwich. It is deliberately not a production shape: one instance, no
# redundancy, and a database on local disk with no managed backups.

variable "region" {
  description = "AWS region for the demo console."
  type        = string
  default     = "us-west-2"
}

variable "stack_name" {
  description = "Short console name. Resource prefix convoy-console-<stack_name>."
  type        = string
  default     = "demo"
}

variable "environment" {
  description = "Environment label; tags every resource and rides into the containers."
  type        = string
  default     = "demo"
}

variable "domain_name" {
  description = "Apex domain the console is served on; www is served too."
  type        = string
}

variable "bundle_id" {
  description = <<-EOT
    Lightsail bundle. small_3_0 is 2 GB RAM / 2 vCPU, the smallest that runs a
    Next.js production server, a notifier, Postgres, and Caddy at once without
    swapping. micro_3_0 (1 GB) is cheaper and will thrash.
  EOT
  type        = string
  default     = "small_3_0"
}

variable "blueprint_id" {
  description = "Lightsail OS blueprint. Any Ubuntu LTS works; cloud-init installs everything else."
  type        = string
  default     = "ubuntu_22_04"
}

variable "availability_zone" {
  description = "Lightsail AZ. Must be in var.region and carry an 'a'-style suffix, e.g. us-west-2a."
  type        = string
  default     = "us-west-2a"
}

variable "ssh_ingress_cidrs" {
  description = <<-EOT
    CIDRs allowed to reach port 22. Lightsail opens SSH to the world by
    default; this narrows it. Authentication is key-only either way, and the
    console itself never needs port 22.
  EOT
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "control_plane_url" {
  description = "Base HTTPS URL of the runtime control plane the console calls."
  type        = string
}

variable "control_plane_token" {
  description = <<-EOT
    Bearer the console presents on every control-plane call. Generated and
    stored in Secrets Manager by this stack when left null, which is the right
    choice while the control plane is a placeholder that validates nothing.
  EOT
  type        = string
  default     = null
  sensitive   = true
}

variable "workos_api_key" {
  description = "WorkOS API key. Leave null to run with sign-in disabled."
  type        = string
  default     = null
  sensitive   = true
}

variable "workos_client_id" {
  description = "WorkOS client id. A public identifier, but it travels with the key so it lives in the same secret."
  type        = string
  default     = null
  sensitive   = true
}

variable "image_tag" {
  description = "Image tag the instance pulls from ECR on boot."
  type        = string
  default     = "bootstrap"
}

variable "notifier_interval_ms" {
  description = "Notifier poll interval. The default ticks every 2s; a demo does not need that."
  type        = number
  default     = 15000
}

variable "github_repository" {
  description = "owner/name of the repository whose Actions may assume the deploy role."
  type        = string
  default     = "useconvoy/app"
}

variable "github_deploy_branch" {
  description = <<-EOT
    Branch the deploy workflow runs on. Part of the OIDC subject, so a run on
    any other branch — or on a fork — cannot assume the role.
  EOT
  type        = string
  default     = "main"
}
