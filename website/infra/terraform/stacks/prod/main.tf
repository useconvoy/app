# One console deployment = one instantiation of the console-stack module.
# The console is shared and multi-tenant, so there is normally one of these
# per environment — not one per customer. To stamp another environment, copy
# this directory to stacks/<name>/, set the backend key, fill a tfvars file,
# apply.

module "console" {
  source = "../../modules/console-stack"

  stack_name  = var.stack_name
  environment = var.environment

  domain_name        = var.domain_name
  create_hosted_zone = var.create_hosted_zone
  alb_ingress_cidrs  = var.alb_ingress_cidrs

  control_plane_url = var.control_plane_url
  secret_arns       = var.secret_arns

  image_tag = var.image_tag

  # Demo posture. This stamp exists to prove the console works end to end and
  # is expected to be destroyed, so every setting here trades durability for
  # being cheap to create and, more importantly, possible to remove.
  #
  # deletion_protection = false is the load-bearing one: it also empties the
  # image repository on destroy and drops the secret recovery window to zero,
  # which is what makes destroy-then-re-stamp repeatable rather than a
  # once-per-30-days operation.
  #
  # Raise all four before this serves anyone real. Two web tasks stay because
  # one means the ALB has no healthy target while a deploy replaces it, and
  # that is a self-inflicted outage rather than a saving.
  db_multi_az            = false
  single_nat_gateway     = true
  deletion_protection    = false
  web_desired_count      = 2
  notifier_desired_count = 1
}
