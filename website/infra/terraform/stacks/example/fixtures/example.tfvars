# Fixture variables for the CI lint lane's `terraform plan` and as a template
# for a real console tfvars. All ARNs are syntactically valid dummies and the
# domain is reserved for documentation — do NOT apply with these values.

region      = "us-east-1"
stack_name  = "example"
environment = "dev"

domain_name        = "console.example.com"
create_hosted_zone = false

control_plane_url = "https://api.example.com"

image_tag = "bootstrap"

# Issued and rotated outside this module. control_plane_token is required;
# drop the WorkOS pair to run on the app's own sign-in provider, drop the
# Stripe pair to leave billing off.
secret_arns = {
  control_plane_token   = "arn:aws:secretsmanager:us-east-1:123456789012:secret:convoy/console/control-plane-token-AbCdEf"
  workos_api_key        = "arn:aws:secretsmanager:us-east-1:123456789012:secret:convoy/console/workos-api-key-AbCdEf"
  workos_client_id      = "arn:aws:secretsmanager:us-east-1:123456789012:secret:convoy/console/workos-client-id-AbCdEf"
  stripe_secret_key     = "arn:aws:secretsmanager:us-east-1:123456789012:secret:convoy/console/stripe-secret-key-AbCdEf"
  stripe_webhook_secret = "arn:aws:secretsmanager:us-east-1:123456789012:secret:convoy/console/stripe-webhook-secret-AbCdEf"
}
