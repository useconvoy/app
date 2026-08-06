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

  # Production posture; loosen for a throwaway or preview stamp.
  db_multi_az            = true
  single_nat_gateway     = false
  deletion_protection    = true
  web_desired_count      = 2
  notifier_desired_count = 1
}
