# One log group per service (+ sandbox). KMS-encrypted with the stack key;
# CLAUDE.md rule 9 (no secrets or transcript bodies in logs) is an app-layer
# obligation, but retention and encryption are enforced here.

resource "aws_cloudwatch_log_group" "service" {
  for_each = toset(local.services)

  name              = "/convoy/${var.stack_name}/${each.value}"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.stack.arn

  tags = merge(local.tags, { Name = "${local.name_prefix}-${each.value}-logs" })
}
