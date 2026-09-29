# Real provider schema/expressions; every AWS resource is mocked. No account calls.
mock_provider "aws" {
  override_during = plan
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::000000000000:role/mock" }
  }
  mock_resource "aws_lb" {
    defaults = { arn = "arn:aws:elasticloadbalancing:us-west-2:000000000000:loadbalancer/app/example/0000000000000000" }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:us-west-2:000000000000:targetgroup/example/0000000000000000" }
  }
  mock_resource "aws_lb_listener" {
    defaults = { arn = "arn:aws:elasticloadbalancing:us-west-2:000000000000:listener/app/example/0000000000000000/0000000000000000" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-west-2:000000000000:log-group:mock" }
  }
}
override_resource {
  override_during = plan
  target          = aws_iam_role.task
  values          = { id = "model-role", arn = "arn:aws:iam::000000000000:role/model-role" }
}
override_resource {
  override_during = plan
  target          = aws_iam_role.execution
  values          = { id = "execution-role", arn = "arn:aws:iam::000000000000:role/execution-role" }
}
override_resource {
  override_during = plan
  target          = aws_security_group.alb
  values          = { id = "sg-00000000000000001" }
}
override_resource {
  override_during = plan
  target          = aws_security_group.planner
  values          = { id = "sg-00000000000000002" }
}
run "stopped_trial_preserves_authority_and_network_boundaries" {
  command = plan
  assert {
    condition     = aws_ecs_service.planner.desired_count == 0 && aws_ecs_task_definition.planner.cpu == "2048" && aws_ecs_task_definition.planner.memory == "4096" && aws_ecs_task_definition.planner.runtime_platform[0].cpu_architecture == "ARM64"
    error_message = "The trial must be explicitly enabled and use the declared ARM64 CPU allocation."
  }
  assert {
    condition = aws_iam_role_policy.execution.role == aws_iam_role.execution.id && aws_ecs_task_definition.planner.task_role_arn == aws_iam_role.task.arn && toset(flatten([
      for statement in jsondecode(aws_iam_role_policy.execution.policy).Statement : statement.Resource if contains(statement.Action, "secretsmanager:GetSecretValue")
    ])) == toset([var.planner_verification_secret_arn, var.planner_probe_secret_arn])
    error_message = "Only the execution role may fetch exactly the two supplied public-verifier/probe secrets."
  }
  assert {
    condition = toset([for item in jsondecode(aws_ecs_task_definition.planner.container_definitions)[0].secrets : item.name]) == toset(["CONVOY_PLANNER_VERIFICATION_JSON", "CONVOY_PLANNER_PROBE_TOKEN"]) && toset([
      for item in jsondecode(aws_ecs_task_definition.planner.container_definitions)[0].environment : item.name
    ]) == toset(["CONVOY_PLANNER_RELEASE_JSON", "CONVOY_PLANNER_RELEASE_SHA256"])
    error_message = "Planner receives exactly public verification/probe references and the immutable public release; no signer or action authority."
  }
  assert {
    condition = !contains(jsondecode(aws_ecs_task_definition.planner.container_definitions)[0].command, "--manifest") && [
      for item in jsondecode(aws_ecs_task_definition.planner.container_definitions)[0].environment : item.value if item.name == "CONVOY_PLANNER_RELEASE_SHA256"
    ] == [var.release_sha256] && jsondecode(aws_ecs_task_definition.planner.container_definitions)[0].image == var.image
    error_message = "Retain the image entrypoint's one-source release materialization contract and supplied image/release pins."
  }
  assert {
    condition     = aws_vpc_security_group_ingress_rule.planner.referenced_security_group_id == aws_security_group.alb.id && aws_vpc_security_group_ingress_rule.planner.cidr_ipv4 == null && aws_vpc_security_group_ingress_rule.planner.from_port == 8080 && alltrue([for rule in aws_vpc_security_group_ingress_rule.client_https : contains(var.client_cidrs, rule.cidr_ipv4) && rule.from_port == 443 && rule.to_port == 443]) && aws_ecs_service.planner.network_configuration[0].assign_public_ip
    error_message = "Public task IP provides egress only; planner ingress must be ALB-only and HTTPS restricted to the selected client."
  }
  assert {
    condition     = aws_lb_listener.https.protocol == "HTTPS" && aws_lb_listener.https.certificate_arn == var.certificate_arn && aws_lb_target_group.planner.health_check[0].path == "/ready" && strcontains(jsondecode(aws_ecs_task_definition.planner.container_definitions)[0].healthCheck.command[3], "/ready") && jsondecode(aws_ecs_task_definition.planner.container_definitions)[0].healthCheck.startPeriod == 120 && aws_ecs_service.planner.health_check_grace_period_seconds >= 120 && aws_cloudwatch_log_group.planner.retention_in_days == 7
    error_message = "Verified HTTPS and loaded readiness must be explicit in both ECS and ALB, with startup grace and bounded log retention."
  }
}
run "enabled_trial_is_one_task_without_overlap_or_rollback" {
  command = plan
  variables { planner_enabled = true }
  assert {
    condition     = aws_ecs_service.planner.desired_count == 1 && aws_ecs_service.planner.deployment_maximum_percent == 100 && aws_ecs_service.planner.deployment_minimum_healthy_percent == 0 && aws_ecs_service.planner.availability_zone_rebalancing == "DISABLED" && !aws_ecs_service.planner.deployment_circuit_breaker[0].rollback && !aws_ecs_service.planner.enable_execute_command
    error_message = "One stateful session owner requires downtime replacement, no AZ rebalancing or automatic fallback to an old release."
  }
}
run "mutable_image_is_rejected" {
  command = plan
  variables { image = "000000000000.dkr.ecr.us-west-2.amazonaws.com/convoy-planner:latest" }
  expect_failures = [var.image]
}
run "shared_credential_reference_is_rejected" {
  command = plan
  variables { planner_probe_secret_arn = "arn:aws:secretsmanager:us-west-2:000000000000:secret:convoy-trial/public-planner-aaaaaa" }
  expect_failures = [var.planner_probe_secret_arn]
}
