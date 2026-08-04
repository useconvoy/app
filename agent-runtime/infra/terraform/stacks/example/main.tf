# One customer stack = one instantiation of the dedicated-stack module
# (DESIGN §3). To stamp a new customer: copy this directory to
# stacks/<stack-name>/, set the backend key, fill a tfvars file, apply.

module "stack" {
  source = "../../modules/dedicated-stack"

  stack_name  = var.stack_name
  tenant_id   = var.tenant_id
  environment = var.environment

  acm_certificate_arn = var.acm_certificate_arn
  alb_ingress_cidrs   = var.alb_ingress_cidrs

  temporal_address              = var.temporal_address
  temporal_namespace            = var.temporal_namespace
  temporal_mtls_cert_secret_arn = var.temporal_mtls_cert_secret_arn
  temporal_mtls_key_secret_arn  = var.temporal_mtls_key_secret_arn

  image_tag              = var.image_tag
  model_provider_secrets = var.model_provider_secrets
  langfuse_secret_arn    = var.langfuse_secret_arn
  workos_secret_arn      = var.workos_secret_arn

  # Production-stack posture; loosen for throwaway/dev stamps.
  db_multi_az                 = true
  deletion_protection         = true
  control_plane_desired_count = 2
  worker_desired_count        = 2
  litellm_desired_count       = 2
}
