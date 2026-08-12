# The environments service (policy compiler + tool gateway + console API)
# and the connector-sandbox service (provider-shaped Slack/GitHub/Google
# stubs that rehearsals execute against) — the two services the integrated
# demo runs in compose, promoted into the stack. Lean sizing by default
# (see variables); the runtime reaches the gateway through Cloud Map DNS
# (locals.environments_gateway_url) unless var.environments_url overrides.
#
# Hardening noted, not hidden: the environments service currently shares the
# stack's RDS database (its SQLAlchemy tables are disjoint from the runtime
# projections); a dedicated database/role on the same instance is the
# follow-up, mirroring the compose demo profile's separation.

# --- Service discovery ------------------------------------------------------

resource "aws_service_discovery_service" "environments" {
  name = "environments"

  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.this.id
    routing_policy = "MULTIVALUE"
    dns_records {
      type = "A"
      ttl  = 10
    }
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-environments-discovery" })
}

resource "aws_service_discovery_service" "connector_sandbox" {
  name = "sandbox-connectors"

  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.this.id
    routing_policy = "MULTIVALUE"
    dns_records {
      type = "A"
      ttl  = 10
    }
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-connector-sandbox-discovery" })
}

# --- Security groups --------------------------------------------------------

resource "aws_security_group" "environments" {
  name        = "${local.name_prefix}-environments"
  description = "Environments gateway/console: reached by control plane + workers; egress to providers, DB, connector sandbox"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}-environments" })
}

resource "aws_vpc_security_group_ingress_rule" "environments_from_control_plane" {
  security_group_id            = aws_security_group.environments.id
  referenced_security_group_id = aws_security_group.control_plane.id
  from_port                    = var.environments_port
  to_port                      = var.environments_port
  ip_protocol                  = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "environments_from_worker" {
  security_group_id            = aws_security_group.environments.id
  referenced_security_group_id = aws_security_group.worker.id
  from_port                    = var.environments_port
  to_port                      = var.environments_port
  ip_protocol                  = "tcp"

  tags = local.tags
}

# Provider APIs (Slack, Google, GitHub, customer MCP servers) over the NAT.
resource "aws_vpc_security_group_egress_rule" "environments_https_out" {
  security_group_id = aws_security_group.environments.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "environments_to_db" {
  security_group_id            = aws_security_group.environments.id
  referenced_security_group_id = aws_security_group.db.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "environments_to_connector_sandbox" {
  security_group_id            = aws_security_group.environments.id
  referenced_security_group_id = aws_security_group.connector_sandbox.id
  from_port                    = var.connector_sandbox_port
  to_port                      = var.connector_sandbox_port
  ip_protocol                  = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "db_from_environments" {
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = aws_security_group.environments.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"

  tags = local.tags
}

resource "aws_security_group" "connector_sandbox" {
  name        = "${local.name_prefix}-connector-sandbox"
  description = "Provider-shaped stubs: reached only by the environments service; no egress"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}-connector-sandbox" })
}

resource "aws_vpc_security_group_ingress_rule" "connector_sandbox_from_environments" {
  security_group_id            = aws_security_group.connector_sandbox.id
  referenced_security_group_id = aws_security_group.environments.id
  from_port                    = var.connector_sandbox_port
  to_port                      = var.connector_sandbox_port
  ip_protocol                  = "tcp"

  tags = local.tags
}

# Runtime callers need egress to the environments service (mirrors litellm).
resource "aws_vpc_security_group_egress_rule" "control_plane_to_environments" {
  security_group_id            = aws_security_group.control_plane.id
  referenced_security_group_id = aws_security_group.environments.id
  from_port                    = var.environments_port
  to_port                      = var.environments_port
  ip_protocol                  = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "worker_to_environments" {
  security_group_id            = aws_security_group.worker.id
  referenced_security_group_id = aws_security_group.environments.id
  from_port                    = var.environments_port
  to_port                      = var.environments_port
  ip_protocol                  = "tcp"

  tags = local.tags
}

# --- Task definitions -------------------------------------------------------

resource "aws_ecs_task_definition" "environments" {
  family                   = "${local.name_prefix}-environments"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.environments_cpu
  memory                   = var.environments_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.litellm_task.arn # no AWS API surface needed; reuse the no-grants role

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([
    {
      name      = "environments"
      image     = local.image.environments
      essential = true
      portMappings = [
        { containerPort = var.environments_port, protocol = "tcp" }
      ]
      environment = [
        { name = "CONVOY_STACK", value = var.stack_name },
        { name = "CONVOY_TENANT_ID", value = var.tenant_id },
        { name = "CONVOY_ENVIRONMENT", value = var.environment },
        { name = "CONVOY_SERVICE", value = "environments" },
        { name = "CONVOY_GATEWAY_PUBLIC_URL", value = "http://environments.${local.discovery_namespace}:${var.environments_port}/gateway" },
        { name = "CONVOY_CONNECTOR_SANDBOX_URL", value = local.connector_sandbox_url },
        { name = "CONVOY_CONTROL_PLANE_URL", value = var.control_plane_url != "" ? var.control_plane_url : "http://${aws_lb.control_plane.dns_name}" },
      ]
      secrets = [
        { name = "CONVOY_DATABASE_URL", valueFrom = "${aws_secretsmanager_secret.db_credentials.arn}:url::" },
        { name = "CONVOY_MASTER_KEY", valueFrom = aws_secretsmanager_secret.environments_master_key.arn },
        { name = "CONVOY_GATEWAY_SECRET", valueFrom = aws_secretsmanager_secret.environments_gateway_secret.arn },
        { name = "CONVOY_INTERNAL_TOKEN", valueFrom = var.environments_internal_token_secret_arn },
        { name = "CONVOY_CONTROL_PLANE_TOKEN", valueFrom = aws_secretsmanager_secret.runtime_service_token.arn },
      ]
      logConfiguration = local.log_configuration["environments"]
    }
  ])

  tags = merge(local.tags, { Name = "${local.name_prefix}-environments" })
}

resource "aws_ecs_task_definition" "connector_sandbox" {
  family                   = "${local.name_prefix}-connector-sandbox"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.connector_sandbox_cpu
  memory                   = var.connector_sandbox_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.litellm_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([
    {
      name      = "connector-sandbox"
      image     = local.image.connector_sandbox
      essential = true
      portMappings = [
        { containerPort = var.connector_sandbox_port, protocol = "tcp" }
      ]
      environment = [
        { name = "CONVOY_STACK", value = var.stack_name },
        { name = "CONVOY_SERVICE", value = "connector-sandbox" },
        { name = "PORT", value = tostring(var.connector_sandbox_port) },
        { name = "SANDBOX_RUNTIME_ROOT", value = "/var/lib/convoy-sandbox" },
      ]
      logConfiguration = local.log_configuration["connector-sandbox"]
    }
  ])

  tags = merge(local.tags, { Name = "${local.name_prefix}-connector-sandbox" })
}

# --- Services ---------------------------------------------------------------

resource "aws_ecs_service" "environments" {
  name            = "${local.name_prefix}-environments"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.environments.arn
  desired_count   = var.environments_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.environments.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.environments.arn
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    ignore_changes = [task_definition]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-environments" })
}

resource "aws_ecs_service" "connector_sandbox" {
  name            = "${local.name_prefix}-connector-sandbox"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.connector_sandbox.arn
  desired_count   = var.connector_sandbox_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.connector_sandbox.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.connector_sandbox.arn
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    ignore_changes = [task_definition]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-connector-sandbox" })
}
