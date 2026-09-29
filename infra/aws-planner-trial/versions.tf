terraform {
  required_version = "= 1.14.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.62.0"
    }
  }
  # An approved, existing encrypted/versioned backend is required for a real trial.
  # Offline checks use init -backend=false and the mock provider, with no AWS calls.
  backend "s3" {}
}

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = { Project = "Convoy", Environment = var.name, Purpose = "ephemeral-planner-trial", DataClass = "synthetic" }
  }
}
