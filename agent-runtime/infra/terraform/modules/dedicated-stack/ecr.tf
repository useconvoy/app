# Per-stack ECR repositories, one per service image. Namespacing repos under
# the stack keeps the module self-contained for fresh-account stamps; sharing
# a central registry across stacks is a later optimization (see infra/README).

resource "aws_ecr_repository" "this" {
  for_each = toset(local.services)

  name                 = "${local.name_prefix}/${each.value}"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.stack.arn
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-${each.value}" })
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each = aws_ecr_repository.this

  repository = each.value.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep the most recent 25 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 25
        }
        action = { type = "expire" }
      },
    ]
  })
}
