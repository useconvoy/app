# Per-stack secrets. Values are generated here (never hardcoded) and live in
# Secrets Manager under the stack KMS key. NOTE: generated values also exist
# in Terraform state, so this module must only ever be applied against an
# encrypted S3 backend with a KMS key, never with local state.

# --- Temporal payload-codec key --------------------------------------------

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
  description = "LiteLLM proxy master key; the runtime uses it to mint per-run virtual keys with hard dollar caps."
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

# --- Runtime service token --------------------------------------------------
# The bearer the website, schedule firings, and the environments service
# present to the control plane. Generated per stack so a stamped stack never
# boots on the repo's development default.

resource "random_password" "runtime_service_token" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "runtime_service_token" {
  name        = "${local.name_prefix}/runtime-service-token"
  description = "Control-plane service bearer (CONVOY_DEV_TOKEN / CONVOY_CONTROL_PLANE_TOKEN)."
  kms_key_id  = aws_kms_key.stack.arn

  tags = merge(local.tags, { Name = "${local.name_prefix}-runtime-service-token" })
}

resource "aws_secretsmanager_secret_version" "runtime_service_token" {
  secret_id     = aws_secretsmanager_secret.runtime_service_token.id
  secret_string = random_password.runtime_service_token.result
}

# --- Environments service secrets -------------------------------------------
# Master key (Fernet KEK for the credential vault, urlsafe base64 of 32
# bytes) and the run-token signing secret. Both stack-generated; the KMS
# backend later replaces only wrap/unwrap.

resource "random_bytes" "environments_master_key" {
  length = 32
}

resource "aws_secretsmanager_secret" "environments_master_key" {
  name        = "${local.name_prefix}/environments-master-key"
  description = "Fernet KEK for the environments credential vault (CONVOY_MASTER_KEY)."
  kms_key_id  = aws_kms_key.stack.arn

  tags = merge(local.tags, { Name = "${local.name_prefix}-environments-master-key" })
}

resource "aws_secretsmanager_secret_version" "environments_master_key" {
  secret_id = aws_secretsmanager_secret.environments_master_key.id
  # Fernet demands urlsafe base64; translate the standard alphabet.
  secret_string = replace(replace(random_bytes.environments_master_key.base64, "+", "-"), "/", "_")
}

resource "random_password" "environments_gateway_secret" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "environments_gateway_secret" {
  name        = "${local.name_prefix}/environments-gateway-secret"
  description = "HS256 signing secret for per-run gateway tokens (CONVOY_GATEWAY_SECRET)."
  kms_key_id  = aws_kms_key.stack.arn

  tags = merge(local.tags, { Name = "${local.name_prefix}-environments-gateway-secret" })
}

resource "aws_secretsmanager_secret_version" "environments_gateway_secret" {
  secret_id     = aws_secretsmanager_secret.environments_gateway_secret.id
  secret_string = random_password.environments_gateway_secret.result
}
