# Review-only US East (N. Virginia) example; replace every placeholder.
# This is not the user's selected account/region/budget and cannot be applied.
account_id         = "000000000000"
region             = "us-east-1"
availability_zones = ["us-east-1a", "us-east-1b"]
zone_id            = "Z000000000000000"
domains = {
  web       = "console.staging.example.com"
  api       = "api.staging.example.com"
  inference = "inference.staging.example.com"
}
certificate_arns = {
  management = "arn:aws:acm:us-east-1:000000000000:certificate/00000000-0000-0000-0000-000000000001"
  inference  = "arn:aws:acm:us-east-1:000000000000:certificate/00000000-0000-0000-0000-000000000002"
}
images = {
  api       = "000000000000.dkr.ecr.us-east-1.amazonaws.com/convoy-api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  web       = "000000000000.dkr.ecr.us-east-1.amazonaws.com/convoy-web@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  inference = "000000000000.dkr.ecr.us-east-1.amazonaws.com/convoy-reference@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
}
services_enabled = false
budget_usd       = 300
budget_email     = "replace-with-approved-recipient@example.com"

cpu_architecture = "ARM64"

postgres_version          = "17.11"
final_snapshot_identifier = "convoy-v1-staging-final-replace-with-date"
