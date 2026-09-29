# Provider schema and Terraform expressions are real; AWS calls are mocked.
# These plans cannot create resources or establish real AWS readiness.
mock_provider "aws" {
  override_during = plan
  mock_resource "aws_db_instance" {
    defaults = {
      address            = "example.us-east-1.rds.amazonaws.com"
      master_user_secret = [{ secret_arn = "arn:aws:secretsmanager:us-east-1:000000000000:secret:master-example", kms_key_id = "example", secret_status = "active" }]
    }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::000000000000:role/mock" }
  }
  mock_resource "aws_lb" {
    defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:000000000000:loadbalancer/app/example/0000000000000000" }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:000000000000:targetgroup/example/0000000000000000" }
  }
  mock_resource "aws_lb_listener" {
    defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:000000000000:listener/app/example/0000000000000000/0000000000000000" }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws:secretsmanager:us-east-1:000000000000:secret:mock-example" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-east-1:000000000000:log-group:mock" }
  }
}
# Distinct values are essential: a single mock ARN would hide accidental cross-role access.
override_resource {
  override_during = plan
  target          = aws_secretsmanager_secret.this["execution-signing"]
  values          = { arn = "arn:aws:secretsmanager:us-east-1:000000000000:secret:private-signing-example" }
}
override_resource {
  override_during = plan
  target          = aws_secretsmanager_secret.this["action-verification"]
  values          = { arn = "arn:aws:secretsmanager:us-east-1:000000000000:secret:public-action-example" }
}
run "fresh_installation_stays_stopped" {
  command = plan
  assert {
    condition     = alltrue([for service in aws_ecs_service.this : service.desired_count == 0 && !service.network_configuration[0].assign_public_ip]) && !aws_db_instance.this.publicly_accessible
    error_message = "Fresh infrastructure must leave tasks stopped and DB/tasks private before role setup/migration."
  }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.database) == 5 && !contains(keys(aws_vpc_security_group_ingress_rule.database), "inference") && !contains(keys(aws_vpc_security_group_ingress_rule.database), "web")
    error_message = "Only API, scheduler, evaluation and the two explicit DB maintenance tasks may reach Postgres."
  }
  assert {
    condition     = length(jsondecode(aws_ecs_task_definition.this["inference"].container_definitions)[0].secrets) == 2 && length(jsondecode(aws_ecs_task_definition.this["web"].container_definitions)[0].secrets) == 0
    error_message = "Inference receives public verification/probe credentials only; web receives no privileged secret."
  }
  assert {
    condition     = toset(keys(aws_secretsmanager_secret.this)) == toset(["runtime-db", "migration-db", "admin", "execution-signing", "action-verification", "probe"])
    error_message = "Staging uses six secret containers, with no shared execution HMAC."
  }
  assert {
    condition     = toset([for secret in jsondecode(aws_ecs_task_definition.this["api"].container_definitions)[0].secrets : secret.name]) == toset(["CONVOY_DB_PASSWORD", "CONVOY_ADMIN_PASSWORD", "CONVOY_EXECUTION_SIGNING_JSON"]) && toset([for secret in jsondecode(aws_ecs_task_definition.this["inference"].container_definitions)[0].secrets : secret.name]) == toset(["CONVOY_ACTION_VERIFICATION_JSON", "CONVOY_WORKER_PROBE_TOKEN"])
    error_message = "The API signs, inference verifies public action grants, and neither receives the other's key document."
  }
  assert {
    condition = alltrue([for role, policy in aws_iam_role_policy.execution :
      contains(flatten([for statement in jsondecode(policy.policy).Statement : statement.Resource if contains(statement.Action, "secretsmanager:GetSecretValue")]), aws_secretsmanager_secret.this["execution-signing"].arn) == (role == "api")
      && contains(flatten([for statement in jsondecode(policy.policy).Statement : statement.Resource if contains(statement.Action, "secretsmanager:GetSecretValue")]), aws_secretsmanager_secret.this["action-verification"].arn) == (role == "inference")
    ])
    error_message = "Only the API execution role may fetch private signing keys; only inference may fetch public action keys."
  }
  assert {
    condition = alltrue([for role, task in aws_ecs_task_definition.this : alltrue([
      for secret in jsondecode(task.container_definitions)[0].secrets :
      !startswith(secret.name, "CONVOY_EXECUTION_") && !startswith(secret.name, "CONVOY_ACTION_") && !startswith(secret.name, "CONVOY_PLANNER_")
    ]) if !contains(["api", "inference"], role)])
    error_message = "Database jobs, maintenance, and web receive no execution keys."
  }

}
run "enabled_installation_is_single_replica" {
  command = plan
  variables { services_enabled = true }
  assert {
    condition     = alltrue([for service in aws_ecs_service.this : service.desired_count == 1 && service.deployment_maximum_percent == 100]) && length(aws_ecs_service.this) == 5
    error_message = "This staging qualification supports exactly one process per service and no overlapping rollout."
  }
}
run "mutable_images_are_rejected" {
  command = plan
  variables {
    images = {
      api       = "000000000000.dkr.ecr.us-east-1.amazonaws.com/convoy-api:latest"
      web       = "000000000000.dkr.ecr.us-east-1.amazonaws.com/convoy-web:latest"
      inference = "000000000000.dkr.ecr.us-east-1.amazonaws.com/convoy-reference:latest"
    }
  }
  expect_failures = [var.images]
}
