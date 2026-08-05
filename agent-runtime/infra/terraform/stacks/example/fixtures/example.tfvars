# Fixture variables for CI lint-lane `terraform plan` and as a template for
# real stack tfvars. All ARNs are syntactically valid dummies — do NOT apply
# with these values.

region      = "us-east-1"
stack_name  = "example-dev"
tenant_id   = "example"
environment = "dev"

acm_certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000"

temporal_address              = "example-dev.a1b2c.tmprl.cloud:7233"
temporal_namespace            = "example-dev.a1b2c"
temporal_mtls_cert_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:convoy/temporal/example-dev-cert-AbCdEf"
temporal_mtls_key_secret_arn  = "arn:aws:secretsmanager:us-east-1:123456789012:secret:convoy/temporal/example-dev-key-AbCdEf"

image_tag = "bootstrap"

model_provider_secrets = {
  ANTHROPIC_API_KEY = "arn:aws:secretsmanager:us-east-1:123456789012:secret:convoy/providers/anthropic-AbCdEf"
}
