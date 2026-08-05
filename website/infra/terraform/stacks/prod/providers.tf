provider "aws" {
  region = var.region

  default_tags {
    tags = {
      "convoy:stack" = "convoy-console-${var.stack_name}"
      "convoy:env"   = var.environment
      "managed-by"   = "terraform"
    }
  }
}
