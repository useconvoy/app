locals {
  tasks = merge({
    web       = { image = "web", cpu = 256, memory = 512, command = ["node", "server.js"], database = false, secrets = {} }
    api       = { image = "api", cpu = 512, memory = 1024, command = ["python", "/app/aws-runtime/entrypoint.py", "api"], database = true, secrets = { CONVOY_DB_PASSWORD = "runtime-db", CONVOY_ADMIN_PASSWORD = "admin", CONVOY_EXECUTION_SIGNING_JSON = "execution-signing" } }
    scheduler = { image = "api", cpu = 256, memory = 512, command = ["python", "/app/aws-runtime/entrypoint.py", "scheduler"], database = true, secrets = { CONVOY_DB_PASSWORD = "runtime-db" } }
    inference = { image = "inference", cpu = 1024, memory = 2048, command = ["python", "/app/aws-runtime/inference.py"], database = false, secrets = { CONVOY_ACTION_VERIFICATION_JSON = "action-verification", CONVOY_WORKER_PROBE_TOKEN = "probe" } }
    migrate   = { image = "api", cpu = 256, memory = 512, command = ["python", "/app/aws-runtime/entrypoint.py", "migrate"], database = true, secrets = { CONVOY_DB_PASSWORD = "migration-db" } }
    bootstrap = { image = "api", cpu = 256, memory = 512, command = ["python", "/app/aws-runtime/bootstrap.py"], database = true, secrets = { CONVOY_RUNTIME_DB_PASSWORD = "runtime-db", CONVOY_MIGRATION_DB_PASSWORD = "migration-db" } }
    }, var.enable_evaluations ? {
    evaluations = { image = "api", cpu = 256, memory = 512, command = ["python", "/app/aws-runtime/entrypoint.py", "evaluations"], database = true, secrets = { CONVOY_DB_PASSWORD = "runtime-db" } }
  } : {})
  database_tasks  = { for name, task in local.tasks : name => task if task.database }
  services        = { for name, task in local.tasks : name => task if !contains(["bootstrap", "migrate"], name) }
  repository_arns = { for name, image in var.images : name => "arn:aws:ecr:${var.region}:${var.account_id}:repository/${split("@", split(".amazonaws.com/", image)[1])[0]}" }
  secret_arns = merge({ for name, secret in aws_secretsmanager_secret.this : name => secret.arn }, {
    master = aws_db_instance.this.master_user_secret[0].secret_arn
  })
}
resource "aws_ecs_cluster" "this" {
  name = var.name
  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}
resource "aws_cloudwatch_log_group" "task" {
  for_each          = local.tasks
  name              = "/convoy/${var.name}/${each.key}"
  retention_in_days = 14
}
resource "aws_iam_role" "task" {
  for_each = local.tasks
  name     = "${var.name}-${each.key}-task"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole",
    Condition = { StringEquals = { "aws:SourceAccount" = var.account_id }, ArnLike = { "aws:SourceArn" = "arn:aws:ecs:${var.region}:${var.account_id}:*" } }
  }] })
  # Intentionally no attached policies: application processes need no AWS SDK access.
}
resource "aws_iam_role" "execution" {
  for_each           = local.tasks
  name               = "${var.name}-${each.key}-execution"
  assume_role_policy = aws_iam_role.task[each.key].assume_role_policy
}
resource "aws_iam_role_policy" "execution" {
  for_each = local.tasks
  name     = "pull-log-secrets"
  role     = aws_iam_role.execution[each.key].id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"], Resource = [local.repository_arns[each.value.image]] },
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.task[each.key].arn}:*" }
    ], length(each.value.secrets) > 0 ? [{
      Effect   = "Allow", Action = ["secretsmanager:GetSecretValue"],
      Resource = distinct(concat([for secret in values(each.value.secrets) : local.secret_arns[secret]], each.key == "bootstrap" ? [local.secret_arns.master] : []))
  }] : []) })
}
resource "aws_ecs_task_definition" "this" {
  for_each                 = local.tasks
  family                   = "${var.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = aws_iam_role.execution[each.key].arn
  task_role_arn            = aws_iam_role.task[each.key].arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }
  container_definitions = jsonencode([merge({
    name            = each.key, image = var.images[each.value.image], essential = true, command = each.value.command,
    user            = each.key == "web" ? "1001:1001" : "10001:10001",
    linuxParameters = { initProcessEnabled = true }, stopTimeout = 30,
    portMappings    = contains(keys(local.frontends), each.key) ? [{ containerPort = local.frontends[each.key].port, protocol = "tcp" }] : [],
    environment = [for name, value in merge(
      { CONVOY_DATA_DIR = "/data", CONVOY_SIMULATOR = "1", CONVOY_SCHEDULER_INPROCESS = "0", CONVOY_SECURE_COOKIES = "1", CONVOY_LOG_LEVEL = "WARNING" },
      each.value.database ? { PGHOST = aws_db_instance.this.address, PGDATABASE = "convoy", PGUSER = each.key == "migrate" ? "convoy_migrator" : "convoy_app" } : {},
      each.key == "api" ? { CONVOY_PUBLIC_URL = "https://${var.domains.api}", CONVOY_ADMIN_EMAIL = "operator@${var.domains.web}" } : {},
      each.key == "web" ? { CONVOY_API_URL = "https://${var.domains.api}", CONVOY_CONSOLE_ORIGIN = "https://${var.domains.web}" } : {}
    ) : { name = name, value = value }],
    secrets          = concat([for name, secret in each.value.secrets : { name = name, valueFrom = local.secret_arns[secret] }], each.key == "bootstrap" ? [{ name = "CONVOY_MASTER_PASSWORD", valueFrom = "${local.secret_arns.master}:password::" }] : []),
    logConfiguration = { logDriver = "awslogs", options = { awslogs-group = aws_cloudwatch_log_group.task[each.key].name, awslogs-region = var.region, awslogs-stream-prefix = "task", mode = "non-blocking", max-buffer-size = "10m" } }
    }, each.key == "scheduler" ? {
    healthCheck = { command = ["CMD", "python", "/app/aws-runtime/entrypoint.py", "scheduler-health"], interval = 30, timeout = 5, retries = 3, startPeriod = 30 }
  } : {})])
  lifecycle {
    precondition {
      condition     = alltrue([for image in values(var.images) : startswith(image, "${var.account_id}.dkr.ecr.${var.region}.amazonaws.com/")])
      error_message = "Image digests must belong to the approved account and selected region."
    }
  }
}
resource "aws_ecs_service" "this" {
  for_each                           = local.services
  name                               = each.key
  cluster                            = aws_ecs_cluster.this.id
  task_definition                    = aws_ecs_task_definition.this[each.key].arn
  desired_count                      = var.services_enabled ? 1 : 0
  launch_type                        = "FARGATE"
  platform_version                   = "1.4.0"
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  enable_execute_command             = false
  health_check_grace_period_seconds  = contains(keys(local.frontends), each.key) ? 60 : null
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = [aws_subnet.private[0].id]
    security_groups  = [aws_security_group.task[each.key].id]
    assign_public_ip = false
  }
  dynamic "load_balancer" {
    for_each = contains(keys(local.frontends), each.key) ? [local.frontends[each.key]] : []
    content {
      target_group_arn = aws_lb_target_group.this[each.key].arn
      container_name   = each.key
      container_port   = load_balancer.value.port
    }
  }
  depends_on = [aws_lb_listener_rule.this, aws_iam_role_policy.execution]
}
