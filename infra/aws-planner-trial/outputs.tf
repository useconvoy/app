output "planner_url" {
  description = "Usable only after operator-managed DNS and readiness verification; HTTPS must remain verified."
  value       = "https://${var.domain}"
}
output "dns_target" {
  description = "Configure a CNAME, or an A alias using this hosted zone ID, outside this module. Certificate must cover var.domain."
  value       = { name = aws_lb.planner.dns_name, zone_id = aws_lb.planner.zone_id }
}
output "trial" {
  description = "Configuration identity only, not proof that a remote planner is ready or a mission passed."
  value = {
    account_id = var.account_id, region = var.region, cluster = aws_ecs_cluster.trial.name,
    service    = aws_ecs_service.planner.name, task_definition = aws_ecs_task_definition.planner.arn,
    image      = var.image, release_sha256 = var.release_sha256, log_group = aws_cloudwatch_log_group.planner.name
  }
}
