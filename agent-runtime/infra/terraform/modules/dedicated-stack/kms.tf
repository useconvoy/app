# Per-stack KMS key: encrypts the artifact bucket, RDS storage, Secrets Manager
# entries, and CloudWatch log groups. Per-stack keys keep each customer stack's
# data isolated under its own key material.

resource "aws_kms_key" "stack" {
  description             = "Convoy stack key for ${var.stack_name} (S3, RDS, secrets, logs)"
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
            "kms:EncryptionContext:aws:logs:arn" = "arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:/convoy/${var.stack_name}/*"
          }
        }
      },
    ]
  })

  tags = merge(local.tags, { Name = "${local.name_prefix}-stack-key" })
}

resource "aws_kms_alias" "stack" {
  name          = "alias/${local.name_prefix}"
  target_key_id = aws_kms_key.stack.key_id
}
