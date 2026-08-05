# Remote state backend. REQUIRED for real stacks: generated secrets (codec
# key, DB password, LiteLLM master key) exist in state, so state must live in
# an encrypted, access-controlled S3 backend — never on a laptop.
#
# Copy this root per customer stack (stacks/<stack-name>/), then uncomment and
# fill in before `terraform init`:
#
# terraform {
#   backend "s3" {
#     bucket       = "convoy-terraform-state"
#     key          = "agent-runtime/stacks/<stack-name>/terraform.tfstate"
#     region       = "us-east-1"
#     kms_key_id   = "arn:aws:kms:us-east-1:<ops-account>:key/<state-key-id>"
#     encrypt      = true
#     use_lockfile = true
#   }
# }
