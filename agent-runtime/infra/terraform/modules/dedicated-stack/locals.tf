data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name_prefix = "convoy-${var.stack_name}"
  account_id  = data.aws_caller_identity.current.account_id
  partition   = data.aws_partition.current.partition
  region      = data.aws_region.current.region

  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  # Every resource carries these (task constraint: tagged with stack + tenant).
  tags = merge(var.tags, {
    "convoy:stack"  = var.stack_name
    "convoy:tenant" = var.tenant_id
    "convoy:env"    = var.environment
    "managed-by"    = "terraform"
    "convoy:module" = "agent-runtime/dedicated-stack"
  })

  services = ["control-plane", "temporal-worker", "litellm", "sandbox"]

  # Resolved image URIs: explicit override wins, else per-stack ECR repo + tag.
  image = {
    control_plane   = lookup(var.images, "control_plane", "${aws_ecr_repository.this["control-plane"].repository_url}:${var.image_tag}")
    temporal_worker = lookup(var.images, "temporal_worker", "${aws_ecr_repository.this["temporal-worker"].repository_url}:${var.image_tag}")
    litellm         = lookup(var.images, "litellm", "${aws_ecr_repository.this["litellm"].repository_url}:${var.image_tag}")
    sandbox         = lookup(var.images, "sandbox", "${aws_ecr_repository.this["sandbox"].repository_url}:${var.image_tag}")
  }

  # Internal service discovery namespace (Cloud Map).
  discovery_namespace = "${local.name_prefix}.internal"
  litellm_base_url    = "http://litellm.${local.discovery_namespace}:${var.litellm_port}"
}
