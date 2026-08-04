provider "aws" {
  region = var.region

  default_tags {
    tags = {
      "convoy:stack"  = var.stack_name
      "convoy:tenant" = var.tenant_id
      "convoy:env"    = var.environment
      "managed-by"    = "terraform"
    }
  }
}
