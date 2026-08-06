# ---------------------------------------------------------------------------
# Public address
# ---------------------------------------------------------------------------

output "console_url" {
  description = "The address the console is served on once DNS resolves."
  value       = local.console_url
}

output "alb_dns_name" {
  description = "DNS name of the console ALB. The apex and www alias records already point here; this is for verifying resolution and for anything that has to reach the load balancer directly."
  value       = aws_lb.console.dns_name
}

output "hosted_zone_name_servers" {
  description = "Name servers of the console's hosted zone. Set these at the registrar; until they are authoritative, certificate validation and the console's own name will not resolve."
  value       = var.create_hosted_zone ? aws_route53_zone.this[0].name_servers : data.aws_route53_zone.this[0].name_servers
}

output "certificate_arn" {
  description = "ARN of the validated ACM certificate on the HTTPS listener."
  value       = aws_acm_certificate_validation.this.certificate_arn
}

# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------

output "vpc_id" {
  description = "Console VPC id."
  value       = aws_vpc.this.id
}

output "private_subnet_ids" {
  description = "Private subnet ids. Every task runs here, including the one-off migrate task."
  value       = aws_subnet.private[*].id
}

output "task_security_group_id" {
  description = "Security group for the notifier and one-off tasks: no ingress, Postgres and HTTPS egress. run-migrations.sh passes it to RunTask."
  value       = aws_security_group.tasks.id
}

output "web_security_group_id" {
  description = "Security group for web tasks (ALB ingress only)."
  value       = aws_security_group.web.id
}

# ---------------------------------------------------------------------------
# Data store
# ---------------------------------------------------------------------------

output "db_endpoint" {
  description = "Console Postgres endpoint (host:port). Private; reachable only from the task security groups."
  value       = aws_db_instance.this.endpoint
}

output "kms_key_arn" {
  description = "Console KMS key ARN (RDS, secrets, logs)."
  value       = aws_kms_key.console.arn
}

# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

output "db_credentials_secret_arn" {
  description = "Secrets Manager ARN of the Postgres master credentials. Held by the migrate task only."
  value       = aws_secretsmanager_secret.db_credentials.arn
  sensitive   = true
}

output "db_app_credentials_secret_arn" {
  description = "Secrets Manager ARN of the RLS-bound convoy_website_app credentials the web and notifier tasks connect with."
  value       = aws_secretsmanager_secret.db_app_credentials.arn
  sensitive   = true
}

output "session_secret_arn" {
  description = "Secrets Manager ARN of the session signing key."
  value       = aws_secretsmanager_secret.session_secret.arn
  sensitive   = true
}

# ---------------------------------------------------------------------------
# Deploy-pipeline inputs (consumed by infra/deploy/*)
# ---------------------------------------------------------------------------

output "ecr_repository_url" {
  description = "ECR repository for the console image. Tags are immutable: every push needs a new tag."
  value       = aws_ecr_repository.website.repository_url
}

output "cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.this.name
}

output "web_service_name" {
  description = "ECS service name of the web service."
  value       = aws_ecs_service.web.name
}

output "notifier_service_name" {
  description = "ECS service name of the notifier service."
  value       = aws_ecs_service.notifier.name
}

output "migrate_task_definition_arn" {
  description = "Registered migrate task definition (family:revision). No service runs it; run-migrations.sh does, on demand."
  value       = aws_ecs_task_definition.migrate.arn
}
