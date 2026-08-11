# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------

output "vpc_id" {
  description = "Stack VPC id."
  value       = aws_vpc.this.id
}

output "private_subnet_ids" {
  description = "Private subnet ids (all service and sandbox tasks run here)."
  value       = aws_subnet.private[*].id
}

output "public_subnet_ids" {
  description = "Public subnet ids (ALB + NAT only)."
  value       = aws_subnet.public[*].id
}

output "control_plane_alb_dns_name" {
  description = "DNS name of the control-plane ALB — point the customer-facing CNAME here."
  value       = aws_lb.control_plane.dns_name
}

output "control_plane_alb_zone_id" {
  description = "Route53 alias zone id of the control-plane ALB."
  value       = aws_lb.control_plane.zone_id
}

# ---------------------------------------------------------------------------
# Data stores
# ---------------------------------------------------------------------------

output "artifact_bucket_name" {
  description = "Per-stack artifact bucket. Object layout: {tenant}/{env}/..."
  value       = aws_s3_bucket.artifacts.bucket
}

output "artifact_bucket_arn" {
  description = "ARN of the artifact bucket."
  value       = aws_s3_bucket.artifacts.arn
}

output "db_endpoint" {
  description = "RDS Postgres endpoint (host:port)."
  value       = aws_db_instance.this.endpoint
}

output "db_instance_arn" {
  description = "RDS instance ARN."
  value       = aws_db_instance.this.arn
}

output "kms_key_arn" {
  description = "Per-stack KMS key ARN (S3, RDS, secrets, logs)."
  value       = aws_kms_key.stack.arn
}

# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

output "codec_key_secret_arn" {
  description = "Secrets Manager ARN of the Temporal payload-codec key."
  value       = aws_secretsmanager_secret.codec_key.arn
}

output "litellm_master_key_secret_arn" {
  description = "Secrets Manager ARN of the LiteLLM master key."
  value       = aws_secretsmanager_secret.litellm_master_key.arn
}

output "db_credentials_secret_arn" {
  description = "Secrets Manager ARN of the stack DB credentials."
  value       = aws_secretsmanager_secret.db_credentials.arn
}

# ---------------------------------------------------------------------------
# ECS / deploy-pipeline inputs (consumed by infra/deploy/*)
# ---------------------------------------------------------------------------

output "ecs_cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.this.name
}

output "ecs_cluster_arn" {
  description = "ECS cluster ARN."
  value       = aws_ecs_cluster.this.arn
}

output "ecr_repository_urls" {
  description = "Per-service ECR repository URLs (control-plane, temporal-worker, litellm, sandbox)."
  value       = { for name, repo in aws_ecr_repository.this : name => repo.repository_url }
}

output "control_plane_service_name" {
  description = "ECS service name of the control plane."
  value       = aws_ecs_service.control_plane.name
}

output "litellm_service_name" {
  description = "ECS service name of the LiteLLM proxy."
  value       = aws_ecs_service.litellm.name
}

output "worker_bootstrap_service_name" {
  description = "ECS service name of the bootstrap Temporal worker service (build-id-suffixed; later build ids are deploy-pipeline-managed)."
  value       = aws_ecs_service.worker_bootstrap.name
}

output "worker_task_family" {
  description = "Task-definition family the worker deploy pipeline clones per build id."
  value       = aws_ecs_task_definition.worker.family
}

output "temporal_task_queue" {
  description = "Task queue the stack's workers poll; input to the versioned deploy pipeline."
  value       = var.temporal_task_queue
}

output "worker_security_group_id" {
  description = "Security group for worker tasks (deploy pipeline reuses it for per-build-id services)."
  value       = aws_security_group.worker.id
}

# ---------------------------------------------------------------------------
# Sandbox substrate (ECS SandboxProvider inputs)
# ---------------------------------------------------------------------------

output "sandbox_task_definition_arn" {
  description = "Registered base sandbox task definition (family:revision)."
  value       = aws_ecs_task_definition.sandbox.arn
}

output "sandbox_task_family" {
  description = "Sandbox task-definition family the ECS SandboxProvider runs."
  value       = aws_ecs_task_definition.sandbox.family
}

output "sandbox_security_group_id" {
  description = "No-ingress sandbox security group with HTTP/S browser and VPC-endpoint egress."
  value       = aws_security_group.sandbox.id
}

output "sandbox_task_role_arn" {
  description = "Sandbox task role — intentionally credential-free; sandboxes get data materialized in, never keys."
  value       = aws_iam_role.sandbox_task.arn
}

# ---------------------------------------------------------------------------
# IAM
# ---------------------------------------------------------------------------

output "data_access_role_arn" {
  description = "Role workers/control plane assume with a {tenant}/{env}-scoped STS session policy (templates/sts-session-policy.json.tpl)."
  value       = aws_iam_role.data_access.arn
}

output "worker_task_role_arn" {
  description = "Temporal worker task role ARN."
  value       = aws_iam_role.worker_task.arn
}

output "control_plane_task_role_arn" {
  description = "Control-plane task role ARN."
  value       = aws_iam_role.control_plane_task.arn
}
