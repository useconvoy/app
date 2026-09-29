locals {
  repository_arn = "arn:aws:ecr:${var.region}:${var.account_id}:repository/${split("@", split(".amazonaws.com/", var.image)[1])[0]}"
  execution_secrets = {
    CONVOY_PLANNER_VERIFICATION_JSON = var.planner_verification_secret_arn
    CONVOY_PLANNER_PROBE_TOKEN       = var.planner_probe_secret_arn
  }
}
resource "aws_ecs_cluster" "trial" {
  name = var.name
  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}
resource "aws_cloudwatch_log_group" "planner" {
  name              = "/convoy/${var.name}/planner"
  retention_in_days = 7
}
resource "aws_iam_role" "task" {
  name = "${var.name}-task"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole",
    Condition = { StringEquals = { "aws:SourceAccount" = var.account_id }, ArnLike = { "aws:SourceArn" = "arn:aws:ecs:${var.region}:${var.account_id}:*" } }
  }] })
  # No attached/inline policies. Model code has no AWS application authority.
}
resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = aws_iam_role.task.assume_role_policy
}
resource "aws_iam_role_policy" "execution" {
  name = "pull-log-public-verifier-probe"
  role = aws_iam_role.execution.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"], Resource = [local.repository_arn] },
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.planner.arn}:*" },
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = values(local.execution_secrets) }
  ] })
}
resource "aws_ecs_task_definition" "planner" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 2048
  memory                   = 4096
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }
  container_definitions = jsonencode([{
    name = "planner", image = var.image, essential = true, user = "10001:10001",
    # Retain the image entrypoint. It consumes public key/release JSON before exec.
    # No --manifest: that would conflict with the injected immutable release.
    command = ["serve", "--assets", "/opt/convoy/assets/assets.json", "--output", "/run/convoy/state",
      "--ctx-size", "2048", "--host", "0.0.0.0", "--port", "8080",
    "--startup-timeout-s", "120", "--stop-timeout-s", "15"],
    linuxParameters = { initProcessEnabled = true }, stopTimeout = 60,
    portMappings    = [{ containerPort = 8080, protocol = "tcp" }],
    environment = [
      { name = "CONVOY_PLANNER_RELEASE_JSON", value = var.release_json },
      { name = "CONVOY_PLANNER_RELEASE_SHA256", value = var.release_sha256 }
    ],
    secrets = [for name, arn in local.execution_secrets : { name = name, valueFrom = arn }],
    healthCheck = {
      command  = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ready', timeout=3).close()"],
      interval = 15, timeout = 5, retries = 3, startPeriod = 120
    },
    logConfiguration = { logDriver = "awslogs", options = {
      awslogs-group         = aws_cloudwatch_log_group.planner.name, awslogs-region = var.region,
      awslogs-stream-prefix = "planner", mode = "non-blocking", max-buffer-size = "1m"
    } }
  }])
}
resource "aws_ecs_service" "planner" {
  name                               = "planner"
  cluster                            = aws_ecs_cluster.trial.id
  task_definition                    = aws_ecs_task_definition.planner.arn
  desired_count                      = var.planner_enabled ? 1 : 0
  launch_type                        = "FARGATE"
  platform_version                   = "1.4.0"
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  availability_zone_rebalancing      = "DISABLED"
  enable_execute_command             = false
  health_check_grace_period_seconds  = 150
  deployment_circuit_breaker {
    enable   = true
    rollback = false
  }
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.planner.id]
    assign_public_ip = true
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.planner.arn
    container_name   = "planner"
    container_port   = 8080
  }
  depends_on = [aws_lb_listener_rule.planner, aws_iam_role_policy.execution, aws_route_table_association.public]
}
