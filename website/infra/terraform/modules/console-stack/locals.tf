data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name_prefix = "convoy-console-${var.stack_name}"
  account_id  = data.aws_caller_identity.current.account_id
  partition   = data.aws_partition.current.partition
  region      = data.aws_region.current.region

  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  # Every resource carries these. There is no tenant tag: the console is one
  # shared multi-tenant deployment, and tagging it with a tenant would claim
  # an isolation boundary it does not have.
  tags = merge(var.tags, {
    "convoy:stack"  = local.name_prefix
    "convoy:env"    = var.environment
    "managed-by"    = "terraform"
    "convoy:module" = "website/console-stack"
  })

  # web and notifier are services; migrate is a task definition run on demand.
  # All three come from one image and one log-group naming scheme.
  services = ["web", "notifier", "migrate"]

  image = coalesce(var.image, "${aws_ecr_repository.website.repository_url}:${var.image_tag}")

  console_url = "https://${var.domain_name}"

  # Both names live on the certificate and both resolve to the ALB, so a link
  # to either one works and neither redirects to the other.
  served_domains = [var.domain_name, "www.${var.domain_name}"]

  hosted_zone_id = var.create_hosted_zone ? aws_route53_zone.this[0].zone_id : data.aws_route53_zone.this[0].zone_id
}
