output "cluster" { value = aws_ecs_cluster.this.name }
output "urls" { value = { for key, value in var.domains : key => "https://${value}" } }
output "secret_arns" {
  description = "Metadata only. Populate passwords and distinct private-signing/public-action documents out of band; Terraform never reads values."
  value       = { for key, value in aws_secretsmanager_secret.this : key => value.arn }
}
output "operator_email" { value = "operator@${var.domains.web}" }
output "database_endpoint" { value = aws_db_instance.this.address }
output "one_off_tasks" {
  value = { for name in ["bootstrap", "migrate"] : name => {
    task_definition = aws_ecs_task_definition.this[name].arn
    network_configuration = { awsvpcConfiguration = {
      subnets = [aws_subnet.private[0].id], securityGroups = [aws_security_group.task[name].id], assignPublicIp = "DISABLED"
    } }
  } }
}

output "deployment_identity" {
  value = { account_id = var.account_id, region = var.region }
}
