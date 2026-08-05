# ECS cluster + the three long-running Fargate services (control plane,
# Temporal workers, LiteLLM proxy). Sandbox capacity is in sandbox.tf — it is
# task-definition-only; the ECS SandboxProvider RunTasks on demand.

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

# --- Internal service discovery (Cloud Map) --------------------------------

resource "aws_service_discovery_private_dns_namespace" "this" {
  name        = local.discovery_namespace
  description = "Internal discovery for the ${var.stack_name} stack"
  vpc         = aws_vpc.this.id

  tags = merge(local.tags, { Name = local.discovery_namespace })
}

resource "aws_service_discovery_service" "litellm" {
  name = "litellm"

  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.this.id
    routing_policy = "MULTIVALUE"

    dns_records {
      type = "A"
      ttl  = 10
    }
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-litellm-discovery" })
}

# --- Security groups -------------------------------------------------------

resource "aws_security_group" "control_plane" {
  name        = "${local.name_prefix}-control-plane"
  description = "Control-plane tasks for the ${var.stack_name} stack"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}-control-plane" })
}

resource "aws_vpc_security_group_ingress_rule" "control_plane_from_alb" {
  security_group_id            = aws_security_group.control_plane.id
  description                  = "App port from the ALB only"
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = var.control_plane_port
  to_port                      = var.control_plane_port
  ip_protocol                  = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "control_plane_https" {
  security_group_id = aws_security_group.control_plane.id
  description       = "HTTPS egress (Temporal Cloud, WorkOS, AWS APIs)"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "control_plane_temporal_grpc" {
  security_group_id = aws_security_group.control_plane.id
  description       = "Temporal Cloud gRPC"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 7233
  to_port           = 7233
  ip_protocol       = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "control_plane_to_db" {
  security_group_id            = aws_security_group.control_plane.id
  description                  = "Postgres"
  referenced_security_group_id = aws_security_group.db.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "control_plane_to_litellm" {
  security_group_id            = aws_security_group.control_plane.id
  description                  = "LiteLLM proxy"
  referenced_security_group_id = aws_security_group.litellm.id
  from_port                    = var.litellm_port
  to_port                      = var.litellm_port
  ip_protocol                  = "tcp"

  tags = local.tags
}

resource "aws_security_group" "worker" {
  name        = "${local.name_prefix}-worker"
  description = "Temporal worker tasks for the ${var.stack_name} stack"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}-worker" })
}

resource "aws_vpc_security_group_egress_rule" "worker_https" {
  security_group_id = aws_security_group.worker.id
  description       = "HTTPS egress (AWS APIs via endpoints, S3)"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "worker_temporal_grpc" {
  security_group_id = aws_security_group.worker.id
  description       = "Temporal Cloud gRPC"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 7233
  to_port           = 7233
  ip_protocol       = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "worker_to_db" {
  security_group_id            = aws_security_group.worker.id
  description                  = "Postgres (projections, outbox)"
  referenced_security_group_id = aws_security_group.db.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "worker_to_litellm" {
  security_group_id            = aws_security_group.worker.id
  description                  = "LiteLLM proxy"
  referenced_security_group_id = aws_security_group.litellm.id
  from_port                    = var.litellm_port
  to_port                      = var.litellm_port
  ip_protocol                  = "tcp"

  tags = local.tags
}

resource "aws_security_group" "litellm" {
  name        = "${local.name_prefix}-litellm"
  description = "LiteLLM proxy tasks for the ${var.stack_name} stack"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}-litellm" })
}

resource "aws_vpc_security_group_ingress_rule" "litellm_from_control_plane" {
  security_group_id            = aws_security_group.litellm.id
  description                  = "Proxy port from control plane"
  referenced_security_group_id = aws_security_group.control_plane.id
  from_port                    = var.litellm_port
  to_port                      = var.litellm_port
  ip_protocol                  = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "litellm_from_worker" {
  security_group_id            = aws_security_group.litellm.id
  description                  = "Proxy port from workers"
  referenced_security_group_id = aws_security_group.worker.id
  from_port                    = var.litellm_port
  to_port                      = var.litellm_port
  ip_protocol                  = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "litellm_https" {
  security_group_id = aws_security_group.litellm.id
  description       = "HTTPS egress to model provider APIs"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "litellm_to_db" {
  security_group_id            = aws_security_group.litellm.id
  description                  = "Postgres (virtual key store)"
  referenced_security_group_id = aws_security_group.db.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"

  tags = local.tags
}

# --- Shared container config ----------------------------------------------

locals {
  common_env = [
    { name = "CONVOY_STACK", value = var.stack_name },
    { name = "CONVOY_TENANT_ID", value = var.tenant_id },
    { name = "CONVOY_ENVIRONMENT", value = var.environment },
    { name = "CONVOY_ARTIFACT_BUCKET", value = aws_s3_bucket.artifacts.bucket },
    { name = "CONVOY_DATA_ACCESS_ROLE_ARN", value = aws_iam_role.data_access.arn },
    { name = "TEMPORAL_ADDRESS", value = var.temporal_address },
    { name = "TEMPORAL_NAMESPACE", value = var.temporal_namespace },
    { name = "TEMPORAL_TASK_QUEUE", value = var.temporal_task_queue },
    { name = "LITELLM_BASE_URL", value = local.litellm_base_url },
  ]

  runtime_secrets = concat(
    [
      { name = "DATABASE_URL", valueFrom = "${aws_secretsmanager_secret.db_credentials.arn}:url::" },
      { name = "CONVOY_CODEC_KEY_B64", valueFrom = aws_secretsmanager_secret.codec_key.arn },
      { name = "LITELLM_MASTER_KEY", valueFrom = aws_secretsmanager_secret.litellm_master_key.arn },
      { name = "TEMPORAL_TLS_CERT_PEM", valueFrom = var.temporal_mtls_cert_secret_arn },
      { name = "TEMPORAL_TLS_KEY_PEM", valueFrom = var.temporal_mtls_key_secret_arn },
    ],
    var.langfuse_secret_arn == null ? [] : [
      { name = "LANGFUSE_PUBLIC_KEY", valueFrom = "${var.langfuse_secret_arn}:LANGFUSE_PUBLIC_KEY::" },
      { name = "LANGFUSE_SECRET_KEY", valueFrom = "${var.langfuse_secret_arn}:LANGFUSE_SECRET_KEY::" },
      { name = "LANGFUSE_HOST", valueFrom = "${var.langfuse_secret_arn}:LANGFUSE_HOST::" },
    ],
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

# --- Control plane ---------------------------------------------------------

resource "aws_ecs_task_definition" "control_plane" {
  family                   = "${local.name_prefix}-control-plane"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.control_plane_cpu
  memory                   = var.control_plane_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.control_plane_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([
    {
      name      = "control-plane"
      image     = local.image.control_plane
      essential = true
      portMappings = [
        { containerPort = var.control_plane_port, protocol = "tcp" }
      ]
      environment = concat(local.common_env, [
        { name = "CONVOY_SERVICE", value = "control-plane" },
        { name = "PORT", value = tostring(var.control_plane_port) },
      ])
      secrets = concat(
        local.runtime_secrets,
        var.workos_secret_arn == null ? [] : [
          { name = "WORKOS_API_KEY", valueFrom = "${var.workos_secret_arn}:WORKOS_API_KEY::" },
          { name = "WORKOS_CLIENT_ID", valueFrom = "${var.workos_secret_arn}:WORKOS_CLIENT_ID::" },
        ],
      )
      logConfiguration = local.log_configuration["control-plane"]
    }
  ])

  tags = merge(local.tags, { Name = "${local.name_prefix}-control-plane" })
}

resource "aws_ecs_service" "control_plane" {
  name            = "${local.name_prefix}-control-plane"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.control_plane.arn
  desired_count   = var.control_plane_desired_count
  launch_type     = "FARGATE"

  health_check_grace_period_seconds = 60

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.control_plane.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.control_plane.arn
    container_name   = "control-plane"
    container_port   = var.control_plane_port
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # Image rollouts are done by infra/deploy/deploy-service.sh registering new
  # task-definition revisions; Terraform must not roll them back.
  lifecycle {
    ignore_changes = [task_definition]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-control-plane" })

  depends_on = [aws_lb_listener.https]
}

# --- Temporal workers ------------------------------------------------------
#
# Worker deploys are BUILD-ID VERSIONED: each build id gets its own ECS
# service so old workers keep draining pinned runs while new runs land on the
# new build — a deploy mid-run must never break replay. Terraform owns only
# the bootstrap service and the task family; infra/deploy/deploy-workers.sh
# creates per-build-id services and infra/deploy/drain-old-workers.sh retires
# them.

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name_prefix}-temporal-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.worker_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([
    {
      name      = "temporal-worker"
      image     = local.image.temporal_worker
      essential = true
      environment = concat(local.common_env, [
        { name = "CONVOY_SERVICE", value = "temporal-worker" },
        { name = "TEMPORAL_WORKER_BUILD_ID", value = var.worker_build_id },
        { name = "CONVOY_SANDBOX_PROVIDER", value = "ecs" },
        { name = "CONVOY_SANDBOX_CLUSTER", value = aws_ecs_cluster.this.name },
        { name = "CONVOY_SANDBOX_TASK_FAMILY", value = "${local.name_prefix}-sandbox" },
        { name = "CONVOY_SANDBOX_SUBNETS", value = join(",", aws_subnet.private[*].id) },
        { name = "CONVOY_SANDBOX_SECURITY_GROUP", value = aws_security_group.sandbox.id },
        { name = "CONVOY_SANDBOX_LOG_GROUP", value = aws_cloudwatch_log_group.service["sandbox"].name },
      ])
      secrets          = local.runtime_secrets
      logConfiguration = local.log_configuration["temporal-worker"]
    }
  ])

  tags = merge(local.tags, { Name = "${local.name_prefix}-temporal-worker" })
}

resource "aws_ecs_service" "worker_bootstrap" {
  name            = "${local.name_prefix}-temporal-worker-${var.worker_build_id}"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # The deploy pipeline owns worker rollout after bootstrap: new build ids get
  # new services; this one is drained to 0 by drain-old-workers.sh once its
  # pinned runs finish. Terraform must not fight either operation.
  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }

  tags = merge(local.tags, {
    Name              = "${local.name_prefix}-temporal-worker-${var.worker_build_id}"
    "convoy:build-id" = var.worker_build_id
  })
}

# --- LiteLLM proxy ---------------------------------------------------------

resource "aws_ecs_task_definition" "litellm" {
  family                   = "${local.name_prefix}-litellm"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.litellm_cpu
  memory                   = var.litellm_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.litellm_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([
    {
      name      = "litellm"
      image     = local.image.litellm
      essential = true
      portMappings = [
        { containerPort = var.litellm_port, protocol = "tcp" }
      ]
      environment = [
        { name = "CONVOY_STACK", value = var.stack_name },
        { name = "CONVOY_TENANT_ID", value = var.tenant_id },
        { name = "CONVOY_ENVIRONMENT", value = var.environment },
        { name = "CONVOY_SERVICE", value = "litellm" },
        { name = "PORT", value = tostring(var.litellm_port) },
        { name = "STORE_MODEL_IN_DB", value = "True" },
      ]
      secrets = concat(
        [
          { name = "LITELLM_MASTER_KEY", valueFrom = aws_secretsmanager_secret.litellm_master_key.arn },
          { name = "DATABASE_URL", valueFrom = "${aws_secretsmanager_secret.db_credentials.arn}:url::" },
        ],
        [
          for env_name, secret_arn in var.model_provider_secrets :
          { name = env_name, valueFrom = secret_arn }
        ],
      )
      logConfiguration = local.log_configuration["litellm"]
    }
  ])

  tags = merge(local.tags, { Name = "${local.name_prefix}-litellm" })
}

resource "aws_ecs_service" "litellm" {
  name            = "${local.name_prefix}-litellm"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.litellm.arn
  desired_count   = var.litellm_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.litellm.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.litellm.arn
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    ignore_changes = [task_definition]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-litellm" })
}
