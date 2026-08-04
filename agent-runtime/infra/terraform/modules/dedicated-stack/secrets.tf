# Per-stack secrets. Values are generated here (never hardcoded) and live in
# Secrets Manager under the stack KMS key. NOTE: generated values also exist
# in Terraform state — the stamping runbook requires an encrypted S3+KMS
# backend for state (see infra/README.md).

# --- Temporal payload-codec key (DESIGN §3: codec on from day one) ---------

resource "random_bytes" "codec_key" {
  length = 32
}

resource "aws_secretsmanager_secret" "codec_key" {
  name        = "${local.name_prefix}/payload-codec-key"
  description = "Per-stack AES-256 key for the Temporal payload codec (base64). Temporal Cloud sees ciphertext + refs only."
  kms_key_id  = aws_kms_key.stack.arn

  tags = merge(local.tags, { Name = "${local.name_prefix}-codec-key" })
}

resource "aws_secretsmanager_secret_version" "codec_key" {
  secret_id     = aws_secretsmanager_secret.codec_key.id
  secret_string = random_bytes.codec_key.base64
}

# --- LiteLLM proxy master key ----------------------------------------------

resource "random_password" "litellm_master_key" {
  length  = 40
  special = false
}

resource "aws_secretsmanager_secret" "litellm_master_key" {
  name        = "${local.name_prefix}/litellm-master-key"
  description = "LiteLLM proxy master key; the runtime uses it to mint per-run virtual keys with hard dollar caps (DESIGN §11)."
  kms_key_id  = aws_kms_key.stack.arn

  tags = merge(local.tags, { Name = "${local.name_prefix}-litellm-master-key" })
}

resource "aws_secretsmanager_secret_version" "litellm_master_key" {
  secret_id     = aws_secretsmanager_secret.litellm_master_key.id
  secret_string = "sk-${random_password.litellm_master_key.result}"
}

# --- Database credentials --------------------------------------------------

resource "aws_secretsmanager_secret" "db_credentials" {
  name        = "${local.name_prefix}/db-credentials"
  description = "Stack Postgres credentials and connection info."
  kms_key_id  = aws_kms_key.stack.arn

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
    url      = "postgresql://${aws_db_instance.this.username}:${random_password.db_master.result}@${aws_db_instance.this.address}:${aws_db_instance.this.port}/${aws_db_instance.this.db_name}"
  })
}
