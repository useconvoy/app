# ECS cluster, the two long-running services, and the migrate task
# definition. web and notifier are the same image under different commands;
# migrate is that image again with no service behind it, run on demand by
# infra/deploy/run-migrations.sh so the database never has to be exposed to
# reach it.

resource "aws_ecs_cluster" "this" {
  name = local.name_prefix

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = merge(local.tags, { Name = local.name_prefix })
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

# --- Security groups -------------------------------------------------------

resource "aws_security_group" "web" {
  name        = "${local.name_prefix}-web"
  description = "Console web tasks for ${var.stack_name}"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}-web" })
}

resource "aws_vpc_security_group_ingress_rule" "web_from_alb" {
  security_group_id            = aws_security_group.web.id
  description                  = "App port from the ALB only"
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = var.web_port
  to_port                      = var.web_port
  ip_protocol                  = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "web_https" {
  security_group_id = aws_security_group.web.id
  description       = "HTTPS egress (control plane, WorkOS, Stripe, AWS APIs)"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "web_to_db" {
  security_group_id            = aws_security_group.web.id
  description                  = "Postgres"
  referenced_security_group_id = aws_security_group.db.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"

  tags = local.tags
}

# The notifier and the one-off tasks share a group because they need the same
# thing and neither takes ingress: reach Postgres, reach HTTPS, accept
# nothing. Splitting them would produce two identical rule sets.
resource "aws_security_group" "tasks" {
  name        = "${local.name_prefix}-tasks"
  description = "Console notifier and one-off tasks for ${var.stack_name}: no ingress"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}-tasks" })
}

resource "aws_vpc_security_group_egress_rule" "tasks_https" {
  security_group_id = aws_security_group.tasks.id
  description       = "HTTPS egress (control-plane event feed, AWS APIs)"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "tasks_to_db" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "Postgres"
  referenced_security_group_id = aws_security_group.db.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"

  tags = local.tags
}

# --- Shared container config ----------------------------------------------

locals {
  # Plain environment. The control-plane URL is a public address, not a
  # credential; its bearer token is injected separately below.
  common_env = [
    { name = "NODE_ENV", value = "production" },
    { name = "CONVOY_ENVIRONMENT", value = var.environment },
    { name = "CONVOY_CONTROL_PLANE_URL", value = var.control_plane_url },
  ]

  # Injected by the execution role from Secrets Manager. Nothing here is ever
  # a literal value, and the app-role DSN — not the master one — is what the
  # running services get, so row-level security binds every query they make.
  app_secrets = [
    { name = "WEBSITE_PG_DSN", valueFrom = "${aws_secretsmanager_secret.db_app_credentials.arn}:url::" },
    { name = "CONVOY_CONTROL_PLANE_TOKEN", valueFrom = var.secret_arns["control_plane_token"] },
  ]

  # Absent keys leave the feature off rather than half-configured: no WorkOS
  # pair means the app's own sign-in provider, no Stripe pair means the
  # billing surfaces stay dark and the webhook answers 503.
  optional_web_secrets = concat(
    contains(keys(var.secret_arns), "workos_api_key") ? [
      { name = "WORKOS_API_KEY", valueFrom = var.secret_arns["workos_api_key"] }
    ] : [],
    contains(keys(var.secret_arns), "workos_client_id") ? [
      { name = "WORKOS_CLIENT_ID", valueFrom = var.secret_arns["workos_client_id"] }
    ] : [],
    contains(keys(var.secret_arns), "stripe_secret_key") ? [
      { name = "STRIPE_SECRET_KEY", valueFrom = var.secret_arns["stripe_secret_key"] }
    ] : [],
    contains(keys(var.secret_arns), "stripe_webhook_secret") ? [
      { name = "STRIPE_WEBHOOK_SECRET", valueFrom = var.secret_arns["stripe_webhook_secret"] }
    ] : [],
  )

  log_configuration = {
    for svc in local.services : svc => {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.service[svc].name
        "awslogs-region"        = local.region
        "awslogs-stream-prefix" = svc
      }
    }
  }
}

# --- Web -------------------------------------------------------------------

resource "aws_ecs_task_definition" "web" {
  family                   = "${local.name_prefix}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.web_cpu
  memory                   = var.web_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.web_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([
    {
      name      = "web"
      image     = local.image
      essential = true
      command   = ["npm", "run", "start"]
      portMappings = [
        { containerPort = var.web_port, protocol = "tcp" }
      ]
      environment = concat(local.common_env, [
        { name = "CONVOY_SERVICE", value = "web" },
        { name = "PORT", value = tostring(var.web_port) },
        # Without this the server binds loopback and the ALB health check
        # never reaches it.
        { name = "HOSTNAME", value = "0.0.0.0" },
      ])
      secrets = concat(
        local.app_secrets,
        [{ name = "SESSION_SECRET", valueFrom = aws_secretsmanager_secret.session_secret.arn }],
        local.optional_web_secrets,
      )
      logConfiguration = local.log_configuration["web"]
    }
  ])

  tags = merge(local.tags, { Name = "${local.name_prefix}-web" })
}

resource "aws_ecs_service" "web" {
  name            = "${local.name_prefix}-web"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = var.web_desired_count
  launch_type     = "FARGATE"

  health_check_grace_period_seconds = 60

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.web.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = var.web_port
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # Image rollouts register new task-definition revisions from
  # infra/deploy/deploy-service.sh. Terraform must not drag the service back
  # to the revision it stamped.
  lifecycle {
    ignore_changes = [task_definition]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-web" })

  depends_on = [aws_lb_listener.https]
}

# --- Notifier --------------------------------------------------------------

resource "aws_ecs_task_definition" "notifier" {
  family                   = "${local.name_prefix}-notifier"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.notifier_cpu
  memory                   = var.notifier_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.notifier_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([
    {
      name      = "notifier"
      image     = local.image
      essential = true
      command   = ["npm", "run", "notifier"]
      environment = concat(local.common_env, [
        { name = "CONVOY_SERVICE", value = "notifier" },
      ])
      secrets          = local.app_secrets
      logConfiguration = local.log_configuration["notifier"]
    }
  ])

  tags = merge(local.tags, { Name = "${local.name_prefix}-notifier" })
}

resource "aws_ecs_service" "notifier" {
  name            = "${local.name_prefix}-notifier"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.notifier.arn
  desired_count   = var.notifier_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    ignore_changes = [task_definition]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-notifier" })
}

# --- Migrations ------------------------------------------------------------
#
# A task definition with no service. The database sits in private subnets with
# no route in, so migrations run from inside the VPC as a one-off Fargate task
# on the same image — the alternative is putting a hole in the network for
# every schema change.
#
# This is the only place the master credentials are handed to anything. The
# app-role password rides along because run-migrations.sh follows the
# migration with infra/db/sync-app-role.mjs, which aligns the RLS-bound role
# with the password Terraform generated; the value never leaves the task.

resource "aws_ecs_task_definition" "migrate" {
  family                   = "${local.name_prefix}-migrate"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.migrate_cpu
  memory                   = var.migrate_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.migrate_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([
    {
      name      = "migrate"
      image     = local.image
      essential = true
      command   = ["npm", "run", "db:migrate"]
      environment = concat(local.common_env, [
        { name = "CONVOY_SERVICE", value = "migrate" },
      ])
      secrets = [
        { name = "WEBSITE_PG_ADMIN_DSN", valueFrom = "${aws_secretsmanager_secret.db_credentials.arn}:url::" },
        { name = "WEBSITE_PG_APP_PASSWORD", valueFrom = "${aws_secretsmanager_secret.db_app_credentials.arn}:password::" },
      ]
      logConfiguration = local.log_configuration["migrate"]
    }
  ])

  tags = merge(local.tags, { Name = "${local.name_prefix}-migrate" })
}
