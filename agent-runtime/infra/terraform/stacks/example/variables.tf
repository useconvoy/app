# Root inputs for one customer stack. Everything else takes the module's
# documented defaults; override per stack by adding module arguments in
# main.tf (see modules/dedicated-stack/variables.tf for the full surface).

variable "region" {
  description = "AWS region for the stack."
  type        = string
  default     = "us-east-1"
}

variable "stack_name" {
  description = "Short unique stack name, e.g. \"acme-prod\"."
  type        = string
}

variable "tenant_id" {
  description = "Tenant identifier (tags + S3 {tenant}/{env}/... layout)."
  type        = string
}

variable "environment" {
  description = "Environment label, e.g. \"prod\"."
  type        = string
  default     = "prod"
}

variable "acm_certificate_arn" {
  description = "ACM certificate for the control-plane ALB HTTPS listener."
  type        = string
}

variable "alb_ingress_cidrs" {
  description = "CIDRs allowed to reach the control-plane ALB."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "temporal_address" {
  description = "Temporal Cloud gRPC endpoint for this stack's namespace."
  type        = string
}

variable "temporal_namespace" {
  description = "Temporal Cloud namespace dedicated to this stack (Convoy-operated)."
  type        = string
}

variable "temporal_mtls_cert_secret_arn" {
  description = "Secrets Manager ARN of the namespace mTLS client certificate (PEM)."
  type        = string
}

variable "temporal_mtls_key_secret_arn" {
  description = "Secrets Manager ARN of the namespace mTLS private key (PEM)."
  type        = string
}

variable "image_tag" {
  description = "Image tag (git SHA / build id) for the initial service images."
  type        = string
  default     = "bootstrap"
}

variable "model_provider_secrets" {
  description = "Model-provider API keys for LiteLLM: env-var name -> Secrets Manager ARN."
  type        = map(string)
  default     = {}
}

variable "langfuse_secret_arn" {
  description = "Optional Langfuse project keys secret (ops account, per-stack project)."
  type        = string
  default     = null
}

variable "workos_secret_arn" {
  description = "Optional WorkOS credentials secret for the control plane."
  type        = string
  default     = null
}
