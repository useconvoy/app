# Pass-through of the module outputs the runbook and deploy pipeline consume
# (`terraform output -json` is read by infra/deploy/lib/common.sh).

output "control_plane_alb_dns_name" {
  description = "Point the customer-facing CNAME here."
  value       = module.stack.control_plane_alb_dns_name
}

output "artifact_bucket_name" {
  description = "Per-stack artifact bucket ({tenant}/{env}/... layout)."
  value       = module.stack.artifact_bucket_name
}

output "db_endpoint" {
  description = "RDS Postgres endpoint."
  value       = module.stack.db_endpoint
}

output "db_credentials_secret_arn" {
  description = "Secrets Manager ARN of DB credentials."
  value       = module.stack.db_credentials_secret_arn
}

output "codec_key_secret_arn" {
  description = "Secrets Manager ARN of the payload-codec key."
  value       = module.stack.codec_key_secret_arn
}

output "litellm_master_key_secret_arn" {
  description = "Secrets Manager ARN of the LiteLLM master key."
  value       = module.stack.litellm_master_key_secret_arn
}

output "ecs_cluster_name" {
  description = "ECS cluster name (deploy pipeline input)."
  value       = module.stack.ecs_cluster_name
}

output "ecr_repository_urls" {
  description = "Per-service ECR repository URLs (deploy pipeline input)."
  value       = module.stack.ecr_repository_urls
}

output "worker_task_family" {
  description = "Worker task-definition family (deploy pipeline input)."
  value       = module.stack.worker_task_family
}

output "worker_bootstrap_service_name" {
  description = "Bootstrap worker ECS service name."
  value       = module.stack.worker_bootstrap_service_name
}

output "worker_security_group_id" {
  description = "Worker security group (deploy pipeline input)."
  value       = module.stack.worker_security_group_id
}

output "temporal_task_queue" {
  description = "Task queue for versioned worker deploys."
  value       = module.stack.temporal_task_queue
}

output "private_subnet_ids" {
  description = "Private subnets (deploy pipeline input for per-build-id worker services)."
  value       = module.stack.private_subnet_ids
}

output "sandbox_task_family" {
  description = "Sandbox task family for the ECS SandboxProvider."
  value       = module.stack.sandbox_task_family
}

output "sandbox_security_group_id" {
  description = "Sandbox security group id."
  value       = module.stack.sandbox_security_group_id
}

output "data_access_role_arn" {
  description = "Data-access role assumed with tenant/env-scoped session policies."
  value       = module.stack.data_access_role_arn
}
