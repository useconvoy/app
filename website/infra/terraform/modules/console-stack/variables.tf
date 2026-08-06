# ---------------------------------------------------------------------------
# Console identity
# ---------------------------------------------------------------------------

variable "stack_name" {
  description = "Short unique name for this console deployment (e.g. \"prod\", \"staging\"). Used as the resource name prefix convoy-console-<stack_name>; lowercase alphanumerics and hyphens only."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,10}[a-z0-9]$", var.stack_name))
    error_message = "stack_name must be 2-12 chars, lowercase alphanumerics/hyphens, starting with a letter. Twelve is the cap that keeps convoy-console-<stack_name>-alb inside the 32-character load balancer name limit without truncating anything."
  }
}

variable "environment" {
  description = "Environment label for this console (e.g. \"prod\", \"staging\"). Tags every resource and rides into the tasks as NODE_ENV's companion, so log and metric filters can separate deployments."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]{1,32}$", var.environment))
    error_message = "environment must be 1-32 chars of lowercase alphanumerics or hyphens."
  }
}

variable "tags" {
  description = "Additional tags merged onto every resource (stack/env/managed-by are always applied)."
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------------------
# Public identity: domain, certificate, DNS
# ---------------------------------------------------------------------------

variable "domain_name" {
  description = "Apex domain the console and marketing site are served on (e.g. \"convoy.example\"). The certificate covers this name and www.<name>, and both get an alias record at the ALB."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", var.domain_name))
    error_message = "domain_name must be a bare lowercase domain with no scheme, port, or trailing dot."
  }
}

variable "create_hosted_zone" {
  description = "Create the Route 53 public hosted zone for domain_name. True when this apply owns DNS for a freshly registered domain (read hosted_zone_name_servers afterwards and hand them to the registrar); false when the zone already exists and is looked up instead. Certificate validation and the alias records go into the zone either way."
  type        = bool
  default     = false
}

# ---------------------------------------------------------------------------
# Control plane
# ---------------------------------------------------------------------------

variable "control_plane_url" {
  description = <<-EOT
    Base HTTPS URL of the runtime control plane the console calls (e.g.
    "https://api.convoy.example"). The console reaches it over the public
    internet through this VPC's NAT, hitting the runtime stack's public ALB
    rather than any private peering.

    This is a single stack-wide value because every organization is served by
    one shared runtime stack today. Once organizations get dedicated stacks
    the console has to resolve the endpoint per organization instead, and this
    variable becomes a default or disappears; the bearer token in
    secret_arns["control_plane_token"] moves the same way.
  EOT
  type        = string

  validation {
    condition     = can(regex("^https://[^/[:space:]]+(/[^[:space:]]*)?$", var.control_plane_url))
    error_message = "control_plane_url must be an https:// URL with no whitespace."
  }
}

# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------

variable "vpc_cidr" {
  description = "CIDR block for the console VPC. The console has its own VPC in the ops account, separate from every runtime stack."
  type        = string
  default     = "10.60.0.0/16"

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
  description = "Route every private subnet through one NAT gateway (cheaper) instead of one per AZ (survives losing an AZ). Leave true for staging and cost-sensitive stamps; set false for production, where an AZ outage would otherwise cut the notifier and every control-plane call off from the internet."
  type        = bool
  default     = true
}

variable "alb_ingress_cidrs" {
  description = "CIDR blocks allowed to reach the ALB. The default is the whole internet, which is the intended posture: this ALB serves the public marketing site, the sign-in flow, and the Stripe webhook endpoint, none of which can be reached from a known address range. Narrow it only for a private preview deployment."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "alb_idle_timeout" {
  description = "Seconds the ALB holds an idle connection open. Run timelines stream over SSE through the console's proxy route and stay open with long gaps between events; the control plane's keep-alives pass through the proxy untouched, so this only has to outlast the quietest stretch of a live run."
  type        = number
  default     = 300

  validation {
    condition     = var.alb_idle_timeout >= 60 && var.alb_idle_timeout <= 4000
    error_message = "alb_idle_timeout must be between 60 and 4000 seconds."
  }
}

# ---------------------------------------------------------------------------
# Database (RDS Postgres; schema and RLS come from app migrations)
# ---------------------------------------------------------------------------

variable "db_engine_version" {
  description = "RDS Postgres engine version. A bare major (\"16\") lets AWS pick the current minor at create time and lines up with the parameter group family."
  type        = string
  default     = "16"

  validation {
    condition     = can(regex("^16(\\.[0-9]+)?$", var.db_engine_version))
    error_message = "db_engine_version must be \"16\" or a 16.x version; the parameter group family is postgres16."
  }
}

variable "db_instance_class" {
  description = "RDS instance class. The console database holds identity, notifications, feedback, catalog, and billing rows only, never run or plan data, so it stays small."
  type        = string
  default     = "db.t4g.small"
}

variable "db_allocated_storage" {
  description = "Initial RDS storage in GiB. Storage autoscaling raises it up to db_max_allocated_storage without an outage."
  type        = number
  default     = 20
}

variable "db_max_allocated_storage" {
  description = "Upper bound for RDS storage autoscaling, in GiB."
  type        = number
  default     = 200
}

variable "db_multi_az" {
  description = "Run the database with a standby in a second AZ. False keeps a staging stamp cheap; production should set it true, because every sign-in and every notification write goes through this instance."
  type        = bool
  default     = false
}

variable "db_backup_retention_days" {
  description = "Automated backup retention in days."
  type        = number
  default     = 14
}

variable "db_name" {
  description = "Name of the initial Postgres database the console's migrations run against."
  type        = string
  default     = "convoy_website"
}

variable "db_ca_bundle_path" {
  description = "Path inside the container image to the Amazon RDS certificate bundle. The generated DSNs ask for full certificate verification and name this file as the trust anchor, so it has to match where the Dockerfile puts it; change both together or connections fail."
  type        = string
  default     = "/app/certs/rds-global-bundle.pem"
}

variable "db_performance_insights_enabled" {
  description = "Enable RDS Performance Insights. Off by default because the smallest burstable classes reject it — including db.t4g.small, this module's default — and an apply that asks for it there fails outright. Turn it on together with a move to db.t4g.medium or larger."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = <<-EOT
    Whether this stamp is worth protecting from its own operator. True is the
    production answer and makes the stack deliberately hard to remove: the
    database refuses deletion and keeps a final snapshot, the image repository
    refuses to be deleted while it holds images, and deleted secrets sit behind
    a 30-day recovery window.

    False is for throwaway and demo stamps, where all three of those turn a
    `terraform destroy` into manual cleanup. It also makes a destroy/re-stamp
    cycle repeatable: the secret names are derived from the stack name, so a
    pending-deletion secret would otherwise collide with the next apply.
  EOT
  type        = bool
  default     = true
}

# ---------------------------------------------------------------------------
# Image & services
# ---------------------------------------------------------------------------

variable "image_tag" {
  description = "Image tag for the initial task definitions (typically a git SHA). \"bootstrap\" is the tag the stamping runbook pushes first, before any release exists. Later rollouts are done by infra/deploy/deploy-service.sh, not by changing this."
  type        = string
  default     = "bootstrap"
}

variable "image" {
  description = "Full image URI override. When null the per-stack ECR repository and image_tag are used; set it to run the console from a registry this module does not own."
  type        = string
  default     = null
}

variable "cpu_architecture" {
  description = "CPU architecture for Fargate tasks (X86_64 or ARM64). Must match the pushed image."
  type        = string
  default     = "X86_64"

  validation {
    condition     = contains(["X86_64", "ARM64"], var.cpu_architecture)
    error_message = "cpu_architecture must be X86_64 or ARM64."
  }
}

variable "web_desired_count" {
  description = "Desired task count for the web service. Two is the floor for anything users depend on: the ALB needs somewhere to send traffic while a deploy replaces a task."
  type        = number
  default     = 2
}

variable "web_cpu" {
  description = "Fargate CPU units for the web task (256/512/1024/...)."
  type        = number
  default     = 512
}

variable "web_memory" {
  description = "Fargate memory (MiB) for the web task."
  type        = number
  default     = 1024
}

variable "web_port" {
  description = "Container port the Next server listens on."
  type        = number
  default     = 3000
}

variable "web_health_check_path" {
  description = "ALB health-check path. The marketing home page is the cheapest honest signal the app has: it renders server-side without touching the database or the control plane, so it goes unhealthy when the process is broken and stays healthy when a dependency is down."
  type        = string
  default     = "/"
}

variable "notifier_desired_count" {
  description = "Desired task count for the notifier service. One is the right answer: notification writes are idempotent on the event id and each organization's cursor is monotonic, so a second task is safe but does no extra work — it just re-walks the same feed and loses every race."
  type        = number
  default     = 1
}

variable "notifier_cpu" {
  description = "Fargate CPU units for the notifier task."
  type        = number
  default     = 256
}

variable "notifier_memory" {
  description = "Fargate memory (MiB) for the notifier task."
  type        = number
  default     = 512
}

variable "migrate_cpu" {
  description = "Fargate CPU units for the one-off migrate task."
  type        = number
  default     = 512
}

variable "migrate_memory" {
  description = "Fargate memory (MiB) for the one-off migrate task."
  type        = number
  default     = 1024
}

# ---------------------------------------------------------------------------
# Externally-managed secrets
# ---------------------------------------------------------------------------

variable "secret_arns" {
  description = <<-EOT
    Secrets Manager ARNs for credentials this module must never create:
    identity, billing, and the control-plane bearer belong to systems outside
    this stack, and rotating them is their operation, not a terraform apply.
    Referenced by ARN and injected straight into the containers.

    Keys: control_plane_token (required — the console cannot talk to the
    runtime without it), workos_api_key, workos_client_id, stripe_secret_key,
    stripe_webhook_secret. Omitting the WorkOS pair leaves the app on its
    local sign-in provider; omitting the Stripe pair disables the billing
    surfaces and makes the webhook endpoint answer 503.

    Each value must be the whole secret, not a JSON document, because they are
    injected as single environment variables.
  EOT
  type        = map(string)

  validation {
    condition = alltrue([
      for k in keys(var.secret_arns) : contains(
        ["control_plane_token", "workos_api_key", "workos_client_id", "stripe_secret_key", "stripe_webhook_secret"],
      k)
    ])
    error_message = "secret_arns keys must be among: control_plane_token, workos_api_key, workos_client_id, stripe_secret_key, stripe_webhook_secret."
  }

  validation {
    condition     = contains(keys(var.secret_arns), "control_plane_token")
    error_message = "secret_arns must include control_plane_token; every domain call the console makes carries it."
  }

  validation {
    condition = alltrue([
      for v in values(var.secret_arns) : can(regex("^arn:aws[a-zA-Z-]*:secretsmanager:", v))
    ])
    error_message = "every secret_arns value must be a Secrets Manager ARN."
  }
}

# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------

variable "log_retention_days" {
  description = "CloudWatch log retention for the console's log groups."
  type        = number
  default     = 90
}
