terraform {
  required_version = "= 1.14.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.62.0"
    }
  }
  # Supply a pre-existing encrypted, versioned S3 bucket and use_lockfile=true.
  # Local validation uses init -backend=false and never creates state in AWS.
  backend "s3" {}
}

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = { Project = "Convoy", Environment = var.name, DataClass = "synthetic" }
  }
}
