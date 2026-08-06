# Pass-through of the module outputs the runbook and the deploy scripts
# consume (`terraform output -json` is read by infra/deploy/lib/common.sh).

output "console_url" {
  description = "Where the console is served."
  value       = module.console.console_url
}

output "alb_dns_name" {
  description = "Console ALB DNS name."
  value       = module.console.alb_dns_name
}

output "hosted_zone_name_servers" {
  description = "Name servers to set at the registrar."
  value       = module.console.hosted_zone_name_servers
}

output "db_endpoint" {
  description = "Console Postgres endpoint."
  value       = module.console.db_endpoint
}

output "db_credentials_secret_arn" {
  description = "Secrets Manager ARN of the Postgres master credentials."
  value       = module.console.db_credentials_secret_arn
  sensitive   = true
}

output "db_app_credentials_secret_arn" {
  description = "Secrets Manager ARN of the RLS-bound app-role credentials."
  value       = module.console.db_app_credentials_secret_arn
  sensitive   = true
}

output "session_secret_arn" {
  description = "Secrets Manager ARN of the session signing key."
  value       = module.console.session_secret_arn
  sensitive   = true
}

output "ecr_repository_url" {
  description = "ECR repository for the console image (deploy pipeline input)."
  value       = module.console.ecr_repository_url
}

output "cluster_name" {
  description = "ECS cluster name (deploy pipeline input)."
  value       = module.console.cluster_name
}

output "web_service_name" {
  description = "Web ECS service name."
  value       = module.console.web_service_name
}

output "notifier_service_name" {
  description = "Notifier ECS service name."
  value       = module.console.notifier_service_name
}

output "migrate_task_definition_arn" {
  description = "Migrate task definition run on demand by run-migrations.sh."
  value       = module.console.migrate_task_definition_arn
}

output "private_subnet_ids" {
  description = "Private subnets (deploy pipeline input for the one-off migrate task)."
  value       = module.console.private_subnet_ids
}

output "task_security_group_id" {
  description = "No-ingress task security group (deploy pipeline input)."
  value       = module.console.task_security_group_id
}
