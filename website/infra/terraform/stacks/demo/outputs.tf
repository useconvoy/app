output "console_url" {
  description = "Public HTTPS address of the demo console."
  value       = "https://${var.domain_name}"
}

output "static_ip" {
  description = "Elastic address both A records point at."
  value       = aws_lightsail_static_ip.console.ip_address
}

output "instance_name" {
  description = "Lightsail instance name, for `aws lightsail get-instance` and browser SSH."
  value       = aws_lightsail_instance.console.name
}

output "ecr_repository_url" {
  description = "Repository the instance pulls from; push the bootstrap tag here before applying."
  value       = aws_ecr_repository.website.repository_url
}

output "runtime_secret_arn" {
  description = "Secret holding the container environment. Changing it takes effect on the next instance boot."
  value       = aws_secretsmanager_secret.runtime.arn
}

output "bootstrap_log_hint" {
  description = "Where cloud-init records what it did, for when the console does not come up."
  value       = "ssh ubuntu@${aws_lightsail_static_ip.console.ip_address} sudo tail -f /var/log/convoy-bootstrap.log"
}
