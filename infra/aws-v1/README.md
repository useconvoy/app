# AWS CPU staging

Reviewable infrastructure for **one installation with synthetic data**. No AWS
resources have been created or tested by this PR. Terraform validation and
mocked plans do not establish permissions, region capacity, DNS ownership,
image availability, RDS behavior or a hosted service-level objective.

Read [the deployment runbook](../../docs/v1/aws-cpu-staging.md) and
[the costed example](../../docs/v1/aws-cpu-staging-cost.md) before an AWS plan.

## Contents

- `network.tf`: dedicated VPC, two public/private subnets, one NAT, free S3
  gateway endpoint, and service-specific security groups.
- `ingress.tf`: two separate public HTTPS ALBs; web/API share management
  ingress, inference has its own ingress. Existing issued ACM certificates
  and an existing Route53 zone are required inputs.
- `database.tf`: private encrypted single-AZ PostgreSQL, seven-day automated
  backup retention, 14-day logs, secret containers without values, and an
  account-wide budget notification. Retained final snapshots cost money.
- `services.tf`: single-replica Fargate web, API, scheduler, evaluation service
  and CPU scripted inference; explicit bootstrap and migration task definitions.
- `runtime/`: thin image wrappers for verified RDS TLS, database role setup,
  API route availability, and HTTP behind the inference ALB. No model download,
  robot control loop, or customer code runs in the database-privileged job tasks.

The task execution roles pull only their image repository, write only their log
stream and fetch only their specified secrets. Application task roles have no
AWS permissions. There is no shared admin credential in the web/inference task,
no DB credential or database security-group admission in either of those tasks,
and no model package in the API image. AWS `ecr:GetAuthorizationToken` requires
`Resource: "*"`; layer/image access is separately repository-scoped.

## Local checks (no AWS account)

Install Terraform **1.14.7** from HashiCorp's official release, verify the archive
against its official SHA256SUMS, and place it on your task-local PATH. The checked
lockfile pins provider **hashicorp/aws 6.62.0**. Then:

```sh
terraform -chdir=infra/aws-v1 init -backend=false -input=false
terraform -chdir=infra/aws-v1 fmt -check -recursive
terraform -chdir=infra/aws-v1 validate
terraform -chdir=infra/aws-v1 test -var-file=example.tfvars
```

The test provider mocks AWS calls. It evaluates real HCL/provider schemas and
three useful plan contracts: stopped/private initial deployment, single-replica
activation, and rejection of mutable image tags. It never runs a real apply.

After building the qualified base API locally, the wrapper/permission check is:

```sh
docker build -f infra/aws-v1/api.Dockerfile \
  --build-arg BASE_IMAGE=convoy-v1-api:local -t convoy-v1-aws-api:local .
python3 infra/aws-v1/verify_runtime.py --api-image convoy-v1-aws-api:local
```

This creates its own internal Docker network and disposable PostgreSQL 17,
with no published port or persistent volume. It applies real migrations as
`convoy_migrator`, starts the actual API as `convoy_app`, logs in and creates a
project, verifies runtime DDL is denied, blocks legacy routes, repeats grants,
and removes its own containers/network. It does not test AWS's RDS admin role
implementation or TLS endpoint; those remain staged acceptance checks.

## Trust material

`runtime/rds-ca.pem` is the **public** AWS global RDS CA bundle fetched from
<https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem> on September
27, 2026. SHA256:
`e5bb2084ccf45087bda1c9bffdea0eb15ee67f0b91646106e466714f9de3c7e3`.

It contains no private key. Re-fetch, review certificate subjects/expiry and
checksum, rebuild, and qualify before a CA change. Runtime DB connections use
`sslmode=verify-full` and this explicit bundle; there is no TLS fallback.
The application/inference ALB-to-task hop is private HTTP, clearly distinct from
the verified client-to-ALB and app-to-RDS TLS connections.

## Recorded validation

Locally passed on September 27, 2026: Terraform formatting, real provider schema
validation, three mocked plans, the real PostgreSQL role/API check, Python lint,
and the inference derivative listening on 8080 with an authenticated exact-release
probe and rejected unauthenticated probe. Both derivative images built on ARM64.
Provider checksum lock covers darwin_arm64, linux_arm64 and linux_amd64; this
locks the provider, not application-image architecture qualification.

Terraform's Darwin ARM64 archive matched the official SHA256
`e9004e245b3e56bff9c7a6c572295d710f6ce4fc72e3910e99519ebdcb46d1bb`.
AWS provider downloads were verified as signed by HashiCorp. No real provider
plan/apply, AWS credential lookup, cloud secret read/write or resource mutation
was used in these checks. The one-off AWS helper is prepared but has not run.
