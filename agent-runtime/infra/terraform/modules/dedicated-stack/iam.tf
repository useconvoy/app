# IAM. Credential rules:
#   - Trusted workers use refreshable STS credentials by assuming the
#     stack-local data-access role. Its ceiling is this dedicated customer's
#     artifact bucket; sandboxes receive only single-object presigned URLs.
#   - The sandbox task role holds NO credentials to any data store; data is
#     materialized in and artifacts are pulled out by trusted workers.

locals {
  ecs_tasks_assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "ecs-tasks.amazonaws.com" }
        Action    = "sts:AssumeRole"
        Condition = {
          StringEquals = { "aws:SourceAccount" = local.account_id }
        }
      },
    ]
  })

  # Secrets injected into service containers (fetched by the execution role).
  service_injected_secret_arns = compact(concat(
    [
      aws_secretsmanager_secret.codec_key.arn,
      aws_secretsmanager_secret.litellm_master_key.arn,
      aws_secretsmanager_secret.db_credentials.arn,
      var.temporal_mtls_cert_secret_arn,
      var.temporal_mtls_key_secret_arn,
      var.environments_internal_token_secret_arn,
    ],
    values(var.model_provider_secrets),
    var.langfuse_secret_arn == null ? [] : [var.langfuse_secret_arn],
    var.workos_secret_arn == null ? [] : [var.workos_secret_arn],
  ))
}

# ---------------------------------------------------------------------------
# Execution role for the long-running services (pull images, write logs,
# inject secrets). Never visible to application code.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "task_execution" {
  name               = "${local.name_prefix}-task-execution"
  assume_role_policy = local.ecs_tasks_assume_role_policy

  tags = merge(local.tags, { Name = "${local.name_prefix}-task-execution" })
}

resource "aws_iam_role_policy" "task_execution" {
  name = "execution"
  role = aws_iam_role.task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrAuth"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid    = "EcrPull"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
        ]
        Resource = [for r in aws_ecr_repository.this : r.arn]
      },
      {
        Sid    = "ServiceLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = [
          "${aws_cloudwatch_log_group.service["control-plane"].arn}:*",
          "${aws_cloudwatch_log_group.service["temporal-worker"].arn}:*",
          "${aws_cloudwatch_log_group.service["litellm"].arn}:*",
        ]
      },
      {
        Sid      = "InjectSecrets"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = local.service_injected_secret_arns
      },
      {
        Sid      = "DecryptStackKey"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = [aws_kms_key.stack.arn]
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Data-access role: the ONLY application path to this dedicated stack's S3
# bucket. Workers and the control plane assume it with refreshable STS
# credentials; those credentials never cross into sandbox tasks.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "data_access" {
  name                 = "${local.name_prefix}-data-access"
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          AWS = [
            aws_iam_role.worker_task.arn,
            aws_iam_role.control_plane_task.arn,
          ]
        }
        Action = "sts:AssumeRole"
      },
    ]
  })

  tags = merge(local.tags, { Name = "${local.name_prefix}-data-access" })
}

resource "aws_iam_role_policy" "data_access" {
  name = "artifact-store"
  role = aws_iam_role.data_access.id

  # Stack-local ceiling permissions. No s3:DeleteObject — artifacts are never
  # hard-deleted; removal is a tombstone, not a delete.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [aws_s3_bucket.artifacts.arn]
      },
      {
        Sid    = "ObjectReadWrite"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:AbortMultipartUpload",
          "s3:ListMultipartUploadParts",
        ]
        Resource = ["${aws_s3_bucket.artifacts.arn}/*"]
      },
      {
        Sid    = "ArtifactCrypto"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
        ]
        Resource = [aws_kms_key.stack.arn]
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Control-plane task role
# ---------------------------------------------------------------------------

resource "aws_iam_role" "control_plane_task" {
  name               = "${local.name_prefix}-control-plane-task"
  assume_role_policy = local.ecs_tasks_assume_role_policy

  tags = merge(local.tags, { Name = "${local.name_prefix}-control-plane-task" })
}

resource "aws_iam_role_policy" "control_plane_task" {
  name = "control-plane"
  role = aws_iam_role.control_plane_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AssumeDataAccess"
        Effect   = "Allow"
        Action   = ["sts:AssumeRole"]
        Resource = [aws_iam_role.data_access.arn]
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Temporal worker task role
# ---------------------------------------------------------------------------

resource "aws_iam_role" "worker_task" {
  name               = "${local.name_prefix}-worker-task"
  assume_role_policy = local.ecs_tasks_assume_role_policy

  tags = merge(local.tags, { Name = "${local.name_prefix}-worker-task" })
}

resource "aws_iam_role_policy" "worker_task" {
  name = "worker"
  role = aws_iam_role.worker_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AssumeDataAccess"
        Effect   = "Allow"
        Action   = ["sts:AssumeRole"]
        Resource = [aws_iam_role.data_access.arn]
      },
      {
        Sid    = "RunSandboxTasks"
        Effect = "Allow"
        Action = ["ecs:RunTask"]
        Resource = [
          "arn:${local.partition}:ecs:${local.region}:${local.account_id}:task-definition/${local.name_prefix}-sandbox:*",
        ]
        Condition = {
          ArnEquals = { "ecs:cluster" = aws_ecs_cluster.this.arn }
        }
      },
      {
        Sid    = "ManageSandboxTasks"
        Effect = "Allow"
        Action = [
          "ecs:StopTask",
          "ecs:DescribeTasks",
        ]
        Resource = "*"
        Condition = {
          ArnEquals = { "ecs:cluster" = aws_ecs_cluster.this.arn }
        }
      },
      {
        Sid      = "TagSandboxTasks"
        Effect   = "Allow"
        Action   = ["ecs:TagResource"]
        Resource = "*"
        Condition = {
          StringEquals = { "ecs:CreateAction" = "RunTask" }
        }
      },
      {
        Sid    = "PassSandboxRoles"
        Effect = "Allow"
        Action = ["iam:PassRole"]
        Resource = [
          aws_iam_role.sandbox_task.arn,
          aws_iam_role.sandbox_execution.arn,
        ]
        Condition = {
          StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" }
        }
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# LiteLLM task role — no AWS data-plane access; the proxy only talks to
# model endpoints (egress) and Postgres (virtual keys). Secrets arrive via
# the execution role at container start.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "litellm_task" {
  name               = "${local.name_prefix}-litellm-task"
  assume_role_policy = local.ecs_tasks_assume_role_policy

  tags = merge(local.tags, { Name = "${local.name_prefix}-litellm-task" })
}

# ---------------------------------------------------------------------------
# Sandbox roles. The task role is deliberately credential-free: no policies
# granting anything, plus an explicit deny as a tripwire against future drift.
# Sandboxes get data materialized in and artifacts pulled out — never keys.
# The execution role can only pull the sandbox image and write the sandbox
# log group.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "sandbox_task" {
  name               = "${local.name_prefix}-sandbox-task"
  assume_role_policy = local.ecs_tasks_assume_role_policy

  tags = merge(local.tags, { Name = "${local.name_prefix}-sandbox-task" })
}

resource "aws_iam_role_policy" "sandbox_task_deny_all_data" {
  name = "deny-data-stores"
  role = aws_iam_role.sandbox_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SandboxesAreCredentialFree"
        Effect = "Deny"
        Action = [
          "s3:*",
          "secretsmanager:*",
          "kms:*",
          "rds:*",
          "rds-db:*",
          "dynamodb:*",
          "sts:AssumeRole",
          "ssm:*",
          "ecs:*",
          "ecr:*",
          "logs:*",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_iam_role" "sandbox_execution" {
  name               = "${local.name_prefix}-sandbox-execution"
  assume_role_policy = local.ecs_tasks_assume_role_policy

  tags = merge(local.tags, { Name = "${local.name_prefix}-sandbox-execution" })
}

resource "aws_iam_role_policy" "sandbox_execution" {
  name = "sandbox-execution"
  role = aws_iam_role.sandbox_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrAuth"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid    = "PullSandboxImage"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
        ]
        Resource = [aws_ecr_repository.this["sandbox"].arn]
      },
      {
        Sid    = "SandboxLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = ["${aws_cloudwatch_log_group.service["sandbox"].arn}:*"]
      },
    ]
  })
}
