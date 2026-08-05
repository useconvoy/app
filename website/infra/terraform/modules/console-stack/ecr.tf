# One repository: web, notifier, and the migrate task are three commands over
# a single image, so there is exactly one artifact to build, scan, and roll
# back to.

resource "aws_ecr_repository" "website" {
  name = "${local.name_prefix}/website"

  # A deployed tag names one immutable digest forever. Rollback is naming an
  # older tag, and a task definition pinned to a tag cannot be quietly given
  # different bytes underneath it. The cost is that every push needs a fresh
  # tag, including the first `bootstrap` one.
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.console.arn
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-website" })
}

resource "aws_ecr_lifecycle_policy" "website" {
  repository = aws_ecr_repository.website.name

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
