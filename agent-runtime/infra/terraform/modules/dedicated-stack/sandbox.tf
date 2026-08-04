# ECS SandboxProvider prerequisites (DESIGN §13, §16.3).
#
# Sandboxes are per-run Fargate tasks that the worker's ECS SandboxProvider
# launches on demand (RunTask) — there is no long-running sandbox service.
# Two invariants are enforced here, in infra, not in app code:
#
#   1. CREDENTIAL-FREE: the sandbox task role can reach no data store (see
#      iam.tf — empty grants plus explicit deny). Data is materialized into
#      the workspace by trusted workers before exec; artifacts are pulled out
#      by trusted workers after. Model-generated code never sees keys.
#   2. NO NETWORK BEYOND THE SUBSTRATE: the sandbox security group has no
#      ingress at all and egress only to the stack's VPC endpoints (image
#      pull via ECR endpoints + the S3 gateway prefix list, stdout/stderr to
#      the logs endpoint). No internet, no database, no LiteLLM.

resource "aws_security_group" "sandbox" {
  name        = "${local.name_prefix}-sandbox"
  description = "Credential-free sandbox tasks: no ingress; egress to VPC endpoints only"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}-sandbox" })
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
