# One log group per entry point, encrypted with the console key. The app
# writes structured lines carrying counts and ids only — no titles, no
# prompts, no payload bodies, no secrets — but that is an application
# obligation; retention and encryption are enforced here.

resource "aws_cloudwatch_log_group" "service" {
  for_each = toset(local.services)

  name              = "/convoy/console/${var.stack_name}/${each.value}"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.console.arn

  tags = merge(local.tags, { Name = "${local.name_prefix}-${each.value}-logs" })
}
