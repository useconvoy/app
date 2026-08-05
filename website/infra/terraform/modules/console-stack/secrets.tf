# Secrets this module owns. Values are generated here — never written into
# code, tfvars, or a script — and stored in Secrets Manager under the console
# KMS key. Generated values also exist in Terraform state, which is why the
# stamping runbook makes the encrypted S3 backend mandatory.
#
# WorkOS, Stripe, and the control-plane bearer are deliberately absent: they
# are issued and rotated by systems outside this stack and arrive by ARN
# through var.secret_arns.

# --- Session signing key ---------------------------------------------------

resource "random_password" "session_secret" {
  # Session cookies are JWTs signed with HS256 off this value; the app refuses
  # to start below 32 characters, and 64 leaves no argument about entropy.
  length  = 64
  special = false
}

resource "aws_secretsmanager_secret" "session_secret" {
  name        = "${local.name_prefix}/session-secret"
  description = "Signing key for the console's session cookies. Rotating it signs every user out."
  kms_key_id  = aws_kms_key.console.arn

  tags = merge(local.tags, { Name = "${local.name_prefix}-session-secret" })
}

resource "aws_secretsmanager_secret_version" "session_secret" {
  secret_id     = aws_secretsmanager_secret.session_secret.id
  secret_string = random_password.session_secret.result
}

# --- Database credentials --------------------------------------------------
#
# Two roles, two secrets, because they are not interchangeable. The master
# role owns the schema and is what migrations run as. The application connects
# as convoy_website_app, which the migrations create and which cannot bypass
# row-level security — handing the app the master credentials would silently
# disable every policy in the schema.

resource "aws_secretsmanager_secret" "db_credentials" {
  name        = "${local.name_prefix}/db-credentials"
  description = "Console Postgres master credentials. Used by the migrate task only; the running app never holds these."
  kms_key_id  = aws_kms_key.console.arn

  tags = merge(local.tags, { Name = "${local.name_prefix}-db-credentials" })
}

resource "aws_secretsmanager_secret_version" "db_credentials" {
  secret_id = aws_secretsmanager_secret.db_credentials.id
  secret_string = jsonencode({
    username = aws_db_instance.this.username
    password = random_password.db_master.result
    host     = aws_db_instance.this.address
    port     = aws_db_instance.this.port
    dbname   = aws_db_instance.this.db_name
    url      = local.db_master_url
  })
}

resource "random_password" "db_app" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "db_app_credentials" {
  name        = "${local.name_prefix}/db-app-credentials"
  description = "Console Postgres credentials for the RLS-bound convoy_website_app role. The web and notifier tasks connect with these."
  kms_key_id  = aws_kms_key.console.arn

  tags = merge(local.tags, { Name = "${local.name_prefix}-db-app-credentials" })
}

resource "aws_secretsmanager_secret_version" "db_app_credentials" {
  secret_id = aws_secretsmanager_secret.db_app_credentials.id
  secret_string = jsonencode({
    username = local.db_app_username
    password = random_password.db_app.result
    host     = aws_db_instance.this.address
    port     = aws_db_instance.this.port
    dbname   = aws_db_instance.this.db_name
    url      = local.db_app_url
  })
}

locals {
  # The migrations create this role by name; the value is the role's identity,
  # not a credential.
  db_app_username = "convoy_website_app"

  # RDS presents a certificate signed by Amazon's own roots, so asking for
  # verify-full means naming that bundle explicitly — it is shipped in the
  # image at db_ca_bundle_path. Anything weaker would encrypt the connection
  # while accepting whatever answered on port 5432.
  db_ssl_query = "sslmode=verify-full&sslrootcert=${var.db_ca_bundle_path}"

  db_master_url = "postgresql://${aws_db_instance.this.username}:${random_password.db_master.result}@${aws_db_instance.this.address}:${aws_db_instance.this.port}/${aws_db_instance.this.db_name}?${local.db_ssl_query}"
  db_app_url    = "postgresql://${local.db_app_username}:${random_password.db_app.result}@${aws_db_instance.this.address}:${aws_db_instance.this.port}/${aws_db_instance.this.db_name}?${local.db_ssl_query}"
}
