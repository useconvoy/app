# Root inputs for one console deployment. Everything else takes the module's
# documented defaults; override per deployment by adding module arguments in
# main.tf (see modules/console-stack/variables.tf for the full surface).

variable "region" {
  description = "AWS region for the console."
  type        = string
  default     = "us-east-1"
}

variable "stack_name" {
  description = "Short console name, e.g. \"prod\". Resource prefix convoy-console-<stack_name>."
  type        = string
}

variable "environment" {
  description = "Environment label, e.g. \"prod\"."
  type        = string
  default     = "prod"
}

variable "domain_name" {
  description = "Apex domain the console is served on; www is served too."
  type        = string
}

variable "create_hosted_zone" {
  description = "Create the Route 53 hosted zone rather than looking up an existing one."
  type        = bool
  default     = false
}

variable "alb_ingress_cidrs" {
  description = "CIDRs allowed to reach the console ALB. Public by design."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "control_plane_url" {
  description = "Base HTTPS URL of the runtime control plane the console calls."
  type        = string
}

variable "secret_arns" {
  description = "Externally-managed secrets: control_plane_token (required), workos_api_key, workos_client_id, stripe_secret_key, stripe_webhook_secret."
  type        = map(string)
}

variable "image_tag" {
  description = "Image tag (git SHA / build id) for the initial task definitions."
  type        = string
  default     = "bootstrap"
}
