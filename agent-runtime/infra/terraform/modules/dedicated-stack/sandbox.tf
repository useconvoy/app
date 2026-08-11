# ECS sandbox substrate prerequisites for the runtime's SandboxProvider.
#
# Sandboxes are per-run Fargate tasks that the worker's ECS SandboxProvider
# launches on demand (RunTask) — there is no long-running sandbox service.
# Two invariants are enforced here, in infra, not in app code:
#
#   1. CREDENTIAL-FREE: the sandbox task role can reach no data store (see
#      iam.tf — empty grants plus explicit deny). Data moves only over
#      short-lived presigned S3 URLs minted by trusted workers — capability
#      URLs for one object and one verb, handed in via RunTask overrides and
#      job documents. Data in, artifacts out; model-generated code never
#      sees keys.
#   2. BROWSER-ONLY INTERNET SURFACE: the sandbox security group has no
#      ingress and exposes only HTTP/S egress through the private subnet NAT.
#      Chromium is pinned to the runtime's per-session allowlist proxy. The SG
#      provides the underlying HTTP/S route; hard network allowlisting can be
#      added with a dedicated egress proxy/Network Firewall when required.
#      Presigned S3 capabilities remain the sandbox's only storage access.

resource "aws_security_group" "sandbox" {
  name        = "${local.name_prefix}-sandbox"
  description = "Credential-free sandbox tasks: no ingress; HTTP/S browser egress plus VPC endpoints"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}-sandbox" })
}

resource "aws_vpc_security_group_egress_rule" "sandbox_browser_http" {
  security_group_id = aws_security_group.sandbox.id
  description       = "HTTP substrate for allowlisted browser navigation through the in-task proxy"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "sandbox_browser_https" {
  security_group_id = aws_security_group.sandbox.id
  description       = "HTTPS substrate for allowlisted browser navigation through the in-task proxy"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "sandbox_to_vpc_endpoints" {
  security_group_id            = aws_security_group.sandbox.id
  description                  = "HTTPS to interface endpoints (ECR auth/metadata, CloudWatch Logs)"
  referenced_security_group_id = aws_security_group.vpc_endpoints.id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "sandbox_to_s3_gateway" {
  security_group_id = aws_security_group.sandbox.id
  description       = "HTTPS to the S3 gateway endpoint (ECR image layers)"
  prefix_list_id    = aws_vpc_endpoint.s3.prefix_list_id
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"

  tags = local.tags
}

# Base sandbox task definition. The ECS SandboxProvider runs this family (or
# registers sibling revisions per sandbox template — the rendered template
# below is the canonical shape it must preserve).
resource "aws_ecs_task_definition" "sandbox" {
  family                   = "${local.name_prefix}-sandbox"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.sandbox_cpu
  memory                   = var.sandbox_memory
  execution_role_arn       = aws_iam_role.sandbox_execution.arn
  task_role_arn            = aws_iam_role.sandbox_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  ephemeral_storage {
    size_in_gib = var.sandbox_ephemeral_storage_gib
  }

  container_definitions = templatefile("${path.module}/templates/sandbox-task-definition.json.tpl", {
    image      = local.image.sandbox
    stack_name = var.stack_name
    log_group  = aws_cloudwatch_log_group.service["sandbox"].name
    region     = local.region
  })

  tags = merge(local.tags, { Name = "${local.name_prefix}-sandbox" })
}
