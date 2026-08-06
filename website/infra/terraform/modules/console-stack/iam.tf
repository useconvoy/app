# IAM. The console is a front end over the control plane: it holds identity,
# billing, and notification rows in its own database and nothing else. So no
# task role here grants access to any AWS data store, and each carries an
# explicit deny as a tripwire — the day someone reaches for S3 or a runtime
# secret from console code, the deny is what stops it and what makes the
# reason readable.
#
# Secrets are never fetched by application code. The execution role pulls them
# at container start and hands them to the container as environment variables;
# the task roles cannot read them at all.

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

  # Everything the execution role injects: generated here, plus the
  # externally-managed credentials named by ARN.
  injected_secret_arns = concat(
    [
      aws_secretsmanager_secret.session_secret.arn,
      aws_secretsmanager_secret.db_credentials.arn,
      aws_secretsmanager_secret.db_app_credentials.arn,
    ],
    values(var.secret_arns),
  )

  # Console tasks never touch a data store. Denying the whole surface is
  # cheap: nothing here has a legitimate use for any of it.
  task_role_deny_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ConsoleHoldsNoDomainData"
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
          "ecr:*",
        ]
        Resource = "*"
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Execution role — pull the image, write the log streams, inject the secrets.
# Shared by all three task definitions; invisible to application code.
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
        Resource = [aws_ecr_repository.website.arn]
      },
      {
        Sid    = "ConsoleLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = [for svc in local.services : "${aws_cloudwatch_log_group.service[svc].arn}:*"]
      },
      {
        Sid      = "InjectSecrets"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = local.injected_secret_arns
      },
      {
        # Only the console key. Externally-managed secrets encrypted under a
        # different customer-managed key need that key's policy to allow this
        # role — a grant this module cannot make on someone else's key.
        Sid      = "DecryptConsoleKey"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = [aws_kms_key.console.arn]
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Web task role — the browser-facing server. Its only outbound credential is
# the control-plane bearer, which arrives as an environment variable, so it
# needs no AWS permissions whatsoever.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "web_task" {
  name               = "${local.name_prefix}-web-task"
  assume_role_policy = local.ecs_tasks_assume_role_policy

  tags = merge(local.tags, { Name = "${local.name_prefix}-web-task" })
}

resource "aws_iam_role_policy" "web_task_deny_data" {
  name   = "deny-data-stores"
  role   = aws_iam_role.web_task.id
  policy = local.task_role_deny_policy
}

# ---------------------------------------------------------------------------
# Notifier task role — reads the control plane's event feed over HTTPS and
# writes notification rows through the same RLS-bound database role the web
# service uses. Also no AWS permissions.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "notifier_task" {
  name               = "${local.name_prefix}-notifier-task"
  assume_role_policy = local.ecs_tasks_assume_role_policy

  tags = merge(local.tags, { Name = "${local.name_prefix}-notifier-task" })
}

resource "aws_iam_role_policy" "notifier_task_deny_data" {
  name   = "deny-data-stores"
  role   = aws_iam_role.notifier_task.id
  policy = local.task_role_deny_policy
}

# ---------------------------------------------------------------------------
# Migrate task role — separate from the services because it runs as the
# database owner. Same empty AWS surface; the elevated identity it carries is
# a Postgres one, and it lives only for the length of one task.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "migrate_task" {
  name               = "${local.name_prefix}-migrate-task"
  assume_role_policy = local.ecs_tasks_assume_role_policy

  tags = merge(local.tags, { Name = "${local.name_prefix}-migrate-task" })
}

resource "aws_iam_role_policy" "migrate_task_deny_data" {
  name   = "deny-data-stores"
  role   = aws_iam_role.migrate_task.id
  policy = local.task_role_deny_policy
}
