# ---------------------------------------------------------------------------
# Stack identity
# ---------------------------------------------------------------------------

variable "stack_name" {
  description = "Short unique name for this customer stack (e.g. \"acme-prod\"). Used as the resource name prefix; lowercase alphanumerics and hyphens only."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,28}[a-z0-9]$", var.stack_name))
    error_message = "stack_name must be 3-30 chars, lowercase alphanumerics/hyphens, starting with a letter."
  }
}

variable "tenant_id" {
  description = "Tenant identifier for this stack. Every resource is tagged with it and the S3 prefix layout is {tenant}/{env}/... — tenant isolation stays enforced even though the stack is single-customer."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9_-]{0,62}$", var.tenant_id))
    error_message = "tenant_id must be 1-63 chars of lowercase alphanumerics, hyphens, or underscores."
  }
}

variable "environment" {
  description = "Environment label for this stack (e.g. \"prod\", \"staging\"). Second component of the S3 prefix layout {tenant}/{env}/..."
  type        = string
  default     = "prod"

  validation {
    condition     = can(regex("^[a-z0-9-]{1,32}$", var.environment))
    error_message = "environment must be 1-32 chars of lowercase alphanumerics or hyphens."
  }
}

variable "tags" {
  description = "Additional tags merged onto every resource (stack/tenant/env/managed-by are always applied)."
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------

variable "vpc_cidr" {
  description = "CIDR block for the stack VPC."
  type        = string
  default     = "10.40.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "vpc_cidr must be a valid IPv4 CIDR block."
  }
}

variable "az_count" {
  description = "Number of availability zones to spread subnets across."
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 3
    error_message = "az_count must be 2 or 3."
  }
}

variable "single_nat_gateway" {
  description = "Use one NAT gateway for all private subnets (cheaper) instead of one per AZ (more available)."
  type        = bool
  default     = true
}

variable "alb_ingress_cidrs" {
  description = "CIDR blocks allowed to reach the control-plane ALB on 443. The ALB is the only public ingress in the stack."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "acm_certificate_arn" {
  description = "ACM certificate ARN for the control-plane ALB HTTPS listener. Must cover the DNS name pointed at the ALB."
  type        = string
}

# ---------------------------------------------------------------------------
# Database (RDS Postgres 16 + pgvector; RLS enforced at the app layer)
# ---------------------------------------------------------------------------

variable "db_engine_version" {
  description = "RDS Postgres engine version. Must be a 16.x version (pgvector is available on RDS PG16; the app's migrations run CREATE EXTENSION vector)."
  type        = string
  default     = "16.8"

  validation {
    condition     = can(regex("^16\\.", var.db_engine_version))
    error_message = "db_engine_version must be a Postgres 16.x version."
  }
}

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.medium"
}

variable "db_allocated_storage_gb" {
  description = "Initial RDS storage in GiB (autoscaling raises it up to db_max_allocated_storage_gb)."
  type        = number
  default     = 50
}

variable "db_max_allocated_storage_gb" {
  description = "Upper bound for RDS storage autoscaling, in GiB."
  type        = number
  default     = 500
}

variable "db_multi_az" {
  description = "Enable Multi-AZ for RDS. Recommended true for production customer stacks."
  type        = bool
  default     = false
}

variable "db_backup_retention_days" {
  description = "Automated backup retention in days."
  type        = number
  default     = 14
}

variable "db_name" {
  description = "Name of the initial Postgres database."
  type        = string
  default     = "convoy"
}

variable "deletion_protection" {
  description = "Enable deletion protection on the database (and skip-final-snapshot=false). Disable only for throwaway stacks."
  type        = bool
  default     = true
}

# ---------------------------------------------------------------------------
# Temporal Cloud wiring (Convoy-operated; one namespace per stack)
# ---------------------------------------------------------------------------

variable "temporal_address" {
  description = "Temporal Cloud gRPC endpoint for this stack's namespace (e.g. \"acme-prod.a1b2c.tmprl.cloud:7233\")."
  type        = string
}

variable "temporal_namespace" {
  description = "Temporal Cloud namespace dedicated to this stack (e.g. \"acme-prod.a1b2c\")."
  type        = string
}

variable "temporal_mtls_cert_secret_arn" {
  description = "Secrets Manager ARN holding the PEM client certificate for the namespace's mTLS. Provisioned by Convoy ops outside this module; must be readable by this account."
  type        = string

  validation {
    condition     = can(regex("^arn:aws[a-zA-Z-]*:secretsmanager:", var.temporal_mtls_cert_secret_arn))
    error_message = "temporal_mtls_cert_secret_arn must be a Secrets Manager ARN."
  }
}

variable "temporal_mtls_key_secret_arn" {
  description = "Secrets Manager ARN holding the PEM private key for the namespace's mTLS. Provisioned by Convoy ops outside this module; must be readable by this account."
  type        = string

  validation {
    condition     = can(regex("^arn:aws[a-zA-Z-]*:secretsmanager:", var.temporal_mtls_key_secret_arn))
    error_message = "temporal_mtls_key_secret_arn must be a Secrets Manager ARN."
  }
}

variable "temporal_task_queue" {
  description = "Task queue name the stack's workers poll. Also used by the worker-versioned deploy pipeline."
  type        = string
  default     = "agent-runtime"
}

# ---------------------------------------------------------------------------
# Images & services
# ---------------------------------------------------------------------------

variable "image_tag" {
  description = "Default image tag for all services (typically a git SHA / build id). Individual services can be overridden via var.images."
  type        = string
  default     = "bootstrap"
}

variable "images" {
  description = "Optional per-service full image URI overrides. Keys: control_plane, temporal_worker, litellm, sandbox. When unset, the per-stack ECR repo + image_tag is used."
  type        = map(string)
  default     = {}

  validation {
    condition = alltrue([
      for k in keys(var.images) : contains(["control_plane", "temporal_worker", "litellm", "sandbox"], k)
    ])
    error_message = "images keys must be among: control_plane, temporal_worker, litellm, sandbox."
  }
}

variable "cpu_architecture" {
  description = "CPU architecture for Fargate tasks (X86_64 or ARM64). Must match the pushed images."
  type        = string
  default     = "X86_64"

  validation {
    condition     = contains(["X86_64", "ARM64"], var.cpu_architecture)
    error_message = "cpu_architecture must be X86_64 or ARM64."
  }
}

variable "control_plane_desired_count" {
  description = "Desired task count for the control-plane service. Use >= 2 for production stacks."
  type        = number
  default     = 1
}

variable "control_plane_cpu" {
  description = "Fargate CPU units for the control-plane task (256/512/1024/...)."
  type        = number
  default     = 512
}

variable "control_plane_memory" {
  description = "Fargate memory (MiB) for the control-plane task."
  type        = number
  default     = 1024
}

variable "control_plane_port" {
  description = "Container port the control-plane FastAPI app listens on."
  type        = number
  default     = 8000
}

variable "control_plane_health_check_path" {
  description = "ALB health-check path on the control plane."
  type        = string
  default     = "/healthz"
}

variable "worker_desired_count" {
  description = "Desired task count for the Temporal worker service. Use >= 2 for production stacks."
  type        = number
  default     = 1
}

variable "worker_cpu" {
  description = "Fargate CPU units for the worker task."
  type        = number
  default     = 1024
}

variable "worker_memory" {
  description = "Fargate memory (MiB) for the worker task."
  type        = number
  default     = 2048
}

variable "worker_build_id" {
  description = "Temporal worker build id baked into the bootstrap worker service; runs pin to the build that started them. Subsequent build ids are rolled out by the deploy pipeline (infra/deploy/), not by Terraform."
  type        = string
  default     = "bootstrap"
}

variable "litellm_desired_count" {
  description = "Desired task count for the LiteLLM proxy service."
  type        = number
  default     = 1
}

variable "litellm_cpu" {
  description = "Fargate CPU units for the LiteLLM task."
  type        = number
  default     = 512
}

variable "litellm_memory" {
  description = "Fargate memory (MiB) for the LiteLLM task."
  type        = number
  default     = 1024
}

variable "litellm_port" {
  description = "Container port the LiteLLM proxy listens on."
  type        = number
  default     = 4000
}

variable "model_provider_secrets" {
  description = "Model-provider API keys for the LiteLLM proxy; swapping in a customer's own keys is just a different map. Map of environment-variable name to Secrets Manager ARN, e.g. { ANTHROPIC_API_KEY = \"arn:aws:secretsmanager:...\" }. Injected into the LiteLLM container only."
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------------------
# Sandbox capacity (the ECS substrate sandbox tasks run on)
# ---------------------------------------------------------------------------

variable "sandbox_cpu" {
  description = "Default Fargate CPU units for sandbox tasks."
  type        = number
  default     = 1024
}

variable "sandbox_memory" {
  description = "Default Fargate memory (MiB) for sandbox tasks."
  type        = number
  default     = 2048
}

variable "sandbox_ephemeral_storage_gib" {
  description = "Ephemeral workspace storage (GiB) for sandbox tasks. Workspaces are disposable cache — S3 snapshots are the durable truth."
  type        = number
  default     = 50

  validation {
    condition     = var.sandbox_ephemeral_storage_gib >= 21 && var.sandbox_ephemeral_storage_gib <= 200
    error_message = "sandbox_ephemeral_storage_gib must be between 21 and 200 (Fargate limits)."
  }
}

# ---------------------------------------------------------------------------
# Observability & optional integrations
# ---------------------------------------------------------------------------

variable "log_retention_days" {
  description = "CloudWatch log retention for stack log groups."
  type        = number
  default     = 90
}

variable "langfuse_secret_arn" {
  description = "Optional Secrets Manager ARN with Langfuse project keys (ops-account Langfuse, one project per stack). JSON: {\"LANGFUSE_PUBLIC_KEY\":...,\"LANGFUSE_SECRET_KEY\":...,\"LANGFUSE_HOST\":...}. Null disables injection."
  type        = string
  default     = null
}

variable "workos_secret_arn" {
  description = "Optional Secrets Manager ARN with WorkOS credentials for the control plane's auth edge. JSON: {\"WORKOS_API_KEY\":...,\"WORKOS_CLIENT_ID\":...}. Null disables injection."
  type        = string
  default     = null
}
