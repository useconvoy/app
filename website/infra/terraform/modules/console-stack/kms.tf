# One customer-managed key for the whole console stack: RDS storage, the
# secrets this module generates, and the CloudWatch log groups. Keeping the
# console on its own key means revoking it takes the console down and touches
# no runtime stack.

resource "aws_kms_key" "console" {
  description             = "Convoy console key for ${var.stack_name} (RDS, secrets, logs)"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountRootAdmin"
        Effect    = "Allow"
        Principal = { AWS = "arn:${local.partition}:iam::${local.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowCloudWatchLogs"
        Effect    = "Allow"
        Principal = { Service = "logs.${local.region}.amazonaws.com" }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey",
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:/convoy/console/${var.stack_name}/*"
          }
        }
      },
    ]
  })

  tags = merge(local.tags, { Name = "${local.name_prefix}-key" })
}

resource "aws_kms_alias" "console" {
  name          = "alias/${local.name_prefix}"
  target_key_id = aws_kms_key.console.key_id
}
