# The demo console on a single Lightsail instance.
#
# One VM runs four containers behind Caddy: the Next.js server, the notifier,
# Postgres, and Caddy itself terminating TLS from Let's Encrypt. That replaces
# the VPC, NAT gateway, five interface endpoints, ALB, ACM certificate, ECS
# cluster, and RDS instance the previous stack raised — about $200/month of
# managed infrastructure — with roughly $13. That stack is in git history at
# 4f1919a rather than beside this one, so there is one obvious way to deploy.
#
# Everything portable survives the swap: the same image, the same migrations,
# the same environment contract. See README.md for the road back.

locals {
  name_prefix = "convoy-console-${var.stack_name}"

  tags = {
    "convoy:stack" = local.name_prefix
    "convoy:env"   = var.environment
  }

  # Postgres runs on the instance's own disk, reachable only over the compose
  # network, so these DSNs never cross a network boundary the internet can see.
  pg_admin_dsn = "postgres://convoy_website_admin:${urlencode(random_password.pg_admin.result)}@postgres:5432/convoy_website"
  pg_app_dsn   = "postgres://convoy_website_app:${urlencode(random_password.pg_app.result)}@postgres:5432/convoy_website"

  control_plane_token = coalesce(var.control_plane_token, random_password.control_plane_token.result)

  # One secret holds the whole runtime environment. The instance reads it at
  # boot with a narrowly-scoped key, so no secret value is ever written into
  # user_data — which Lightsail shows in its console and hands to anyone who
  # can describe the instance.
  runtime_env = {
    SESSION_SECRET             = random_password.session_secret.result
    WEBSITE_PG_DSN             = local.pg_app_dsn
    WEBSITE_PG_ADMIN_DSN       = local.pg_admin_dsn
    WEBSITE_PG_APP_PASSWORD    = random_password.pg_app.result
    POSTGRES_PASSWORD          = random_password.pg_admin.result
    CONVOY_CONTROL_PLANE_URL   = var.control_plane_url
    CONVOY_CONTROL_PLANE_TOKEN = local.control_plane_token
    NOTIFIER_INTERVAL_MS       = tostring(var.notifier_interval_ms)
    WORKOS_API_KEY             = coalesce(var.workos_api_key, "")
    WORKOS_CLIENT_ID           = coalesce(var.workos_client_id, "")
  }
}

# --- Generated credentials --------------------------------------------------

resource "random_password" "session_secret" {
  length  = 64
  special = false
}

resource "random_password" "pg_admin" {
  length  = 32
  special = false
}

resource "random_password" "pg_app" {
  length  = 32
  special = false
}

# Used only when no token is supplied. The control plane is a placeholder that
# validates nothing, so a generated value is as meaningful as a real one and
# avoids a required input for a stack that should stamp with one variable.
resource "random_password" "control_plane_token" {
  length  = 64
  special = false
}

# --- Image registry ---------------------------------------------------------

resource "aws_ecr_repository" "website" {
  name = "${local.name_prefix}/website"

  # A tag always means the same bytes, so a rollback is exact rather than
  # approximate.
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  # This stamp is meant to be destroyed. Without this, the repository refuses
  # deletion the moment the first image is pushed.
  force_delete = true

  tags = merge(local.tags, { Name = "${local.name_prefix}-website" })
}

resource "aws_ecr_lifecycle_policy" "website" {
  repository = aws_ecr_repository.website.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 10 images."
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

# --- Runtime secret ---------------------------------------------------------

resource "aws_secretsmanager_secret" "runtime" {
  name = "convoy/console/${var.stack_name}/runtime-env"

  description = "Whole runtime environment for the ${var.stack_name} console instance."

  # Zero, not the seven-day minimum: the secret name derives from the stack
  # name, so a recovery window would block re-stamping the demo until it
  # expired. Seven days is the shortest window AWS accepts above zero, which
  # is why repeatable destroy-then-re-stamp needs none at all.
  recovery_window_in_days = 0

  tags = merge(local.tags, { Name = "${local.name_prefix}-runtime-env" })
}

resource "aws_secretsmanager_secret_version" "runtime" {
  secret_id     = aws_secretsmanager_secret.runtime.id
  secret_string = jsonencode(local.runtime_env)
}

# --- Instance identity ------------------------------------------------------
#
# Lightsail instances cannot carry an IAM instance profile the way EC2 does,
# so the only way for the box to reach ECR and Secrets Manager is a key. It is
# scoped to exactly two reads — pull this one repository, read this one secret
# — and nothing else in the account.

# The secret sets no kms_key_id, so it is encrypted under this account's
# AWS-managed Secrets Manager key. Looked up rather than hardcoded: the ARN
# carries an account and region, and one is generated per account.
data "aws_kms_key" "secretsmanager" {
  key_id = "alias/aws/secretsmanager"
}

resource "aws_iam_user" "instance" {
  name = "${local.name_prefix}-instance"
  tags = merge(local.tags, { Name = "${local.name_prefix}-instance" })
}

resource "aws_iam_user_policy" "instance" {
  name = "${local.name_prefix}-instance"
  user = aws_iam_user.instance.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrAuth"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Sid    = "EcrPullThisRepositoryOnly"
        Effect = "Allow"
        Action = [
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchCheckLayerAvailability",
        ]
        Resource = aws_ecr_repository.website.arn
      },
      {
        Sid      = "ReadThisSecretOnly"
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = aws_secretsmanager_secret.runtime.arn
      },
      {
        # GetSecretValue is only half of a secret read. The value is encrypted
        # under the AWS-managed aws/secretsmanager key, and that key's policy
        # delegates to IAM, so the caller needs kms:Decrypt of its own or
        # Secrets Manager answers "Access to KMS is not allowed" — which is a
        # KMS denial wearing a Secrets Manager error message. ViaService keeps
        # the grant narrow: this key, only when Secrets Manager is the one
        # using it, never directly.
        Sid      = "DecryptThatSecret"
        Effect   = "Allow"
        Action   = "kms:Decrypt"
        Resource = data.aws_kms_key.secretsmanager.arn
        Condition = {
          StringEquals = {
            "kms:ViaService" = "secretsmanager.${var.region}.amazonaws.com"
          }
        }
      },
      {
        # NotAction, so every action added above must be repeated here or the
        # explicit Deny overrides its own Allow.
        Sid    = "DenyEverythingElse"
        Effect = "Deny"
        NotAction = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchCheckLayerAvailability",
          "secretsmanager:GetSecretValue",
          "kms:Decrypt",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_iam_access_key" "instance" {
  user = aws_iam_user.instance.name
}

# --- The instance -----------------------------------------------------------

resource "aws_lightsail_instance" "console" {
  name              = local.name_prefix
  availability_zone = var.availability_zone
  blueprint_id      = var.blueprint_id
  bundle_id         = var.bundle_id

  user_data = templatefile("${path.module}/cloud-init.sh.tftpl", {
    aws_region        = var.region
    aws_access_key_id = aws_iam_access_key.instance.id
    aws_secret_key    = aws_iam_access_key.instance.secret
    secret_id         = aws_secretsmanager_secret.runtime.arn
    registry_host     = split("/", aws_ecr_repository.website.repository_url)[0]
    image             = "${aws_ecr_repository.website.repository_url}:${var.image_tag}"
    domain_name       = var.domain_name
    environment       = var.environment
  })

  tags = merge(local.tags, { Name = local.name_prefix })

  # The secret has to hold a value before cloud-init reads it; creating the
  # version is a separate resource from creating the secret.
  # The secret must hold its value before the box boots looking for it, and the
  # key must carry its permissions before the box uses it. Neither is implied
  # by the user_data references (which only need the secret's ARN and the key's
  # id), so without this the instance can race ahead of its own policy and fail
  # the very first secret read.
  depends_on = [
    aws_secretsmanager_secret_version.runtime,
    aws_iam_user_policy.instance,
  ]
}

resource "aws_lightsail_static_ip" "console" {
  name = "${local.name_prefix}-ip"
}

resource "aws_lightsail_static_ip_attachment" "console" {
  static_ip_name = aws_lightsail_static_ip.console.name
  instance_name  = aws_lightsail_instance.console.name
}

resource "aws_lightsail_instance_public_ports" "console" {
  instance_name = aws_lightsail_instance.console.name

  # Caddy needs 80 to answer the ACME HTTP-01 challenge and to redirect; it is
  # never used to serve the app.
  port_info {
    protocol  = "tcp"
    from_port = 80
    to_port   = 80
    cidrs     = ["0.0.0.0/0"]
  }

  port_info {
    protocol  = "tcp"
    from_port = 443
    to_port   = 443
    cidrs     = ["0.0.0.0/0"]
  }

  port_info {
    protocol  = "tcp"
    from_port = 22
    to_port   = 22
    cidrs     = var.ssh_ingress_cidrs
  }
}

# --- DNS --------------------------------------------------------------------

data "aws_route53_zone" "this" {
  name         = "${var.domain_name}."
  private_zone = false
}

resource "aws_route53_record" "apex" {
  zone_id = data.aws_route53_zone.this.zone_id
  name    = var.domain_name
  type    = "A"
  ttl     = 60
  records = [aws_lightsail_static_ip.console.ip_address]
}

resource "aws_route53_record" "www" {
  zone_id = data.aws_route53_zone.this.zone_id
  name    = "www.${var.domain_name}"
  type    = "A"
  ttl     = 60
  records = [aws_lightsail_static_ip.console.ip_address]
}
