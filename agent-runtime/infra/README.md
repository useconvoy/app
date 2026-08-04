# Convoy Agent Runtime — Stack Infrastructure

Operator documentation for stamping, verifying, and deploying **dedicated
customer stacks**: one Terraform apply per customer, in Convoy's AWS account.
Customer-VPC mode reuses the same module against a customer account — nothing
here assumes shared infra.

This README is also the operational runbook: the fresh-account stamp smoke
and the worker-versioned deploy exercise are manual AWS verification gates
and are specified at the end.

---

## Layout

```
infra/
├── terraform/
│   ├── modules/dedicated-stack/   # the reusable module — one instance = one customer stack
│   │   └── templates/             # sandbox task-definition + STS session-policy templates
│   └── stacks/example/            # root that stamps one stack; copy per customer
│       └── fixtures/example.tfvars  # fixture vars for CI plan + tfvars template
└── deploy/                        # image build/push + worker-versioned deploy pipeline
    ├── build-and-push.sh
    ├── deploy-service.sh          # rolling deploys: control-plane, litellm
    ├── deploy-workers.sh          # build-id-versioned worker rollout
    ├── drain-old-workers.sh       # retires unreachable old worker builds
    └── lib/common.sh
```

## What the module stamps

One `module "stack"` instantiation creates, per customer:

| Area | Resources | Notes |
|---|---|---|
| Network | VPC, public subnets (ALB + NAT only), private subnets (all tasks), S3 gateway endpoint, interface endpoints for `ecr.api`, `ecr.dkr`, `logs`, `secretsmanager`, `sts`, `kms` | The **only public ingress is the control-plane ALB, HTTPS only** |
| Data | RDS Postgres 16 (encrypted, TLS-forced, private), per-stack S3 artifact bucket (versioned, SSE-KMS, TLS-only, no public access) | Object layout is `{tenant}/{env}/...`; RLS + `CREATE EXTENSION vector` are applied by app migrations, not Terraform |
| Compute | ECS cluster + Fargate services: control plane (behind ALB), Temporal workers (bootstrap build), LiteLLM proxy (Cloud Map DNS `litellm.convoy-<stack>.internal`), registered **sandbox task definition** (no service — the ECS SandboxProvider `RunTask`s on demand) | |
| Registry | Per-stack ECR repos: `convoy-<stack>/{control-plane,temporal-worker,litellm,sandbox}` | Shared cross-stack registry is a later optimization |
| Secrets | Per-stack payload-codec key (generated), LiteLLM master key (generated), DB credentials — all in Secrets Manager under the per-stack KMS key | No secret value is ever written into code or tfvars |
| IAM | Task roles per service; a **data-access role** workers/control-plane assume with an STS session policy scoped to `s3://<bucket>/{tenant}/{env}/...` (`templates/sts-session-policy.json.tpl`); **sandbox task role with zero data-store access plus an explicit deny** | Every data credential is short-lived and prefix-scoped |
| Observability | KMS-encrypted CloudWatch log groups `/convoy/<stack>/<service>`, Container Insights | Langfuse keys injected when `langfuse_secret_arn` is set |

Convoy-operated, **not** stamped by this module: the Temporal Cloud namespace
(one per stack) and its mTLS client cert/key secrets, WorkOS, the ops-account
Langfuse project, model-provider API-key secrets. They are wired in as
variables.

## Required inputs

Everything else has documented defaults — see
`terraform/modules/dedicated-stack/variables.tf` for the full surface.

| Variable | Meaning |
|---|---|
| `stack_name` | Resource prefix `convoy-<stack_name>`; e.g. `acme-prod` |
| `tenant_id` | Tenant tag + first component of the S3 prefix layout |
| `environment` | Env label + second prefix component (default `prod`) |
| `acm_certificate_arn` | Cert for the ALB HTTPS listener (issue/validate before stamping) |
| `temporal_address`, `temporal_namespace` | The stack's Temporal Cloud namespace endpoint |
| `temporal_mtls_cert_secret_arn`, `temporal_mtls_key_secret_arn` | Secrets Manager ARNs of the namespace mTLS PEMs |
| `model_provider_secrets` | Map of env-var name → secret ARN for LiteLLM (Convoy keys for MVP) |

Sizing/posture knobs worth reviewing per stack: `db_instance_class`,
`db_multi_az`, `*_desired_count`, `alb_ingress_cidrs`, `single_nat_gateway`,
`deletion_protection`.

## Stamping runbook (fresh account or fresh stack)

Prerequisites: Terraform >= 1.6, AWS credentials for the target account,
`aws` CLI v2, `jq`, `docker`, `temporal` CLI. State backend bucket + KMS key
exist in the ops account.

1. **Provision the Convoy-operated pieces** (ops account):
   - Temporal Cloud namespace `<stack>` with mTLS; store cert and key PEMs as
     two Secrets Manager secrets readable by the stack account.
   - Model-provider key secrets for LiteLLM; optional Langfuse project keys
     and WorkOS credentials secrets.
   - ACM certificate for the stack's console/API hostname (DNS-validated).
2. **Create the stack root**: copy `terraform/stacks/example/` to
   `terraform/stacks/<stack-name>/`, uncomment and fill `backend.tf`
   (unique state key per stack; encrypted S3 backend is **mandatory** —
   generated secrets exist in state), and write `<stack>.tfvars` from
   `fixtures/example.tfvars`.
3. **Apply**:
   ```sh
   terraform init
   terraform plan  -var-file=<stack>.tfvars -out=stamp.tfplan
   terraform apply stamp.tfplan
   ```
   First apply takes ~20 minutes (RDS). Record `terraform output -json` —
   the deploy scripts and the runtime's `DeploymentProfile` consume it.
4. **Push images**: for each of `control-plane`, `temporal-worker`,
   `litellm`, `sandbox`:
   ```sh
   STACK_NAME=<stack> AWS_REGION=<region> \
     deploy/build-and-push.sh --service <svc> --tag bootstrap
   ```
   (`bootstrap` matches the module's default `image_tag` and
   `worker_build_id`; the bootstrap worker service starts polling once its
   image exists.) Then register `bootstrap` as the queue's initial default
   build id:
   ```sh
   temporal task-queue update-build-ids add-new-default \
     --task-queue agent-runtime --build-id bootstrap
   ```
5. **Initialize the database**: run the runtime's migrations against
   `db_endpoint` using the `db_credentials` secret (they `CREATE EXTENSION
   vector`, create schemas, and **enable RLS on every table** — every
   connection must set tenant context). The DB is private: run migrations
   from a one-off ECS task or via a bastion pattern, never by exposing the
   DB.
6. **DNS**: point the customer hostname (the one on the ACM cert) at
   `control_plane_alb_dns_name`.
7. **Verification smoke — one linear run lands**: against the stack API,
   `POST /runs` with a small linear goal → watch SSE events → confirm the
   run reaches `completed`, the `LandReport` artifact exists under
   `s3://<artifact_bucket>/<tenant>/<env>/...`, and `GET /runs/{id}` serves
   from projections. If this lands, the stack is live.

### Codec verification (part of the stamp gate)

After the smoke run, fetch the run's history via the Temporal CLI and assert
payloads are ciphertext (no plaintext markers — goal text, tenant id):
`temporal workflow show --workflow-id <run-id> --output json | grep -c '<marker>'`
must be 0. The runtime repo's history-is-ciphertext test automates this; run
it pointed at the live namespace.

## Worker-versioned deploy procedure

Runs are pinned to the worker **build id** that started them; a deploy on day
3 of a 5-day run must not break replay. Therefore workers are never rolled in
place — each build id gets its own ECS service:

```sh
export STACK_NAME=<stack> AWS_REGION=<region>
export TEMPORAL_ADDRESS=... TEMPORAL_NAMESPACE=...
export TEMPORAL_TLS_CERT=/path/client.pem TEMPORAL_TLS_KEY=/path/client.key
BUILD_ID=$(git rev-parse --short=12 HEAD)

deploy/build-and-push.sh --service temporal-worker --tag "${BUILD_ID}"
deploy/deploy-workers.sh --build-id "${BUILD_ID}"   # new service + queue cutover
# ... hours or days later, repeatedly (or on a schedule):
deploy/drain-old-workers.sh                          # retires unreachable builds
deploy/drain-old-workers.sh --delete                 # optionally delete drained services
```

Control plane and LiteLLM are stateless and use plain rolling deploys:

```sh
deploy/build-and-push.sh --service control-plane --tag "${BUILD_ID}"
deploy/deploy-service.sh --service control-plane --tag "${BUILD_ID}"
```

Notes:
- `deploy-workers.sh --skip-temporal-cutover` provisions the new build's
  service without moving the queue default — use it to smoke a build first.
- The SDK worker must start with `build_id = TEMPORAL_WORKER_BUILD_ID` and
  versioning enabled; that env var is set per service by the pipeline.
- Terraform owns the worker task-definition *family* and the bootstrap
  service only (`ignore_changes` on task definition/desired count); per-build
  services are deploy-pipeline-owned. Do not "fix" drift by importing them.
- Rollback = `deploy-workers.sh --build-id <previous>` (idempotent per build
  id); pinned runs are unaffected either way.

## CI lint lane (no AWS credentials required)

```sh
terraform fmt -check -recursive infra/terraform
(cd infra/terraform/modules/dedicated-stack && terraform init -backend=false && terraform validate)
(cd infra/terraform/stacks/example       && terraform init -backend=false && terraform validate)
```

With read-only AWS credentials the lane can additionally run
`terraform plan -var-file=fixtures/example.tfvars` in `stacks/example`.
The committed `.terraform.lock.hcl` files pin providers for
`linux_amd64`; run `terraform providers lock -platform=darwin_arm64 ...` to
extend them for other operator platforms.

## Manual AWS gates

CI has no AWS account, so these are runbook-driven manual gates. Record the
evidence (command output, run id, screenshots) in the PR that changes this
module:

1. **Fresh-account stamp smoke** — steps 1–7 above on a clean account: one
   `terraform apply` → stack up → one linear run lands.
2. **History-is-ciphertext** — codec verification above passes against the
   live namespace.
3. **ECS SandboxProvider contract suite** — `tests/contracts/` (runtime repo)
   run against the stamped stack's sandbox substrate: same suite Local
   passes, pointed at `sandbox_task_family` / `sandbox_security_group_id`.
   Also verify the credential-free invariant from inside a sandbox task:
   `aws s3 ls s3://<artifact_bucket>` and any `sts:AssumeRole` must fail
   (explicit deny), and outbound internet must time out (endpoint-only SG).
4. **Versioned deploy against a live run** — start a multi-step run; mid-run,
   deploy a new worker build via the procedure above; assert the run
   completes on its original build id (`temporal workflow describe` shows
   the pinned build), new runs start on the new build, and
   `drain-old-workers.sh` refuses to retire the old build until the run
   closes.

## Security invariants enforced here

| Invariant | Where |
|---|---|
| STS session policies scope creds to `{tenant}/{env}` | `data-access` role + `templates/sts-session-policy.json.tpl`; the runtime mints per-run sessions with it |
| Sandboxes credential-free, data in / artifacts out | sandbox task role (no grants + explicit deny), no-ingress endpoint-only sandbox SG, separate minimal execution role |
| Payload codec key per stack | generated codec-key secret; the codec itself is runtime code |
| Artifacts never hard-deleted | bucket versioning; no delete grants on the data path; no expiring lifecycle rules |
| No egress assumptions | all external endpoints (Temporal, model gateway, Langfuse, WorkOS) are variables |
| No public ingress except HTTPS ALB | ALB is the only resource in public subnets with an ingress rule; listener is 443-only |
| RLS even in dedicated stacks | app-layer migrations; DB substrate forces TLS and private access — Terraform cannot express RLS |
