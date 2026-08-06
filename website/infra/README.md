# Convoy Console — Deployment Infrastructure

Operator documentation for stamping, verifying, and deploying **the console**:
the marketing site, the portal, and the notification worker, on ECS Fargate in
Convoy's ops account.

The console is **one shared multi-tenant deployment**, not one per customer.
It lives in its own VPC and holds only identity, organizations, notifications,
feedback, catalog, and billing rows; runs, plans, checkpoints, and workspaces
are read through the runtime control plane's single authenticated edge and are
never mirrored here. That is why nothing in this directory resembles the
runtime's per-customer stack: there is no artifact bucket, no Temporal wiring,
no sandbox substrate, and no tenant tag.

This README is also the operational runbook: the stamping sequence and the
manual verification gates are specified below.

---

## Layout

```
website/infra/
├── terraform/
│   ├── modules/console-stack/   # the reusable module — one instance = one console
│   └── stacks/example/          # root that stamps one console; copy per environment
│       └── fixtures/example.tfvars  # fixture vars for CI plan + tfvars template
├── deploy/                      # image build/push + rolling deploy + migrations
│   ├── build-and-push.sh
│   ├── deploy-service.sh        # rolling deploys: web, notifier
│   ├── run-migrations.sh        # one-off ECS task; the DB is never exposed
│   └── lib/common.sh
├── db/sync-app-role.mjs         # baked into the image; run by run-migrations.sh
└── docker/rds-global-bundle.pem # RDS trust anchor, baked into the image
```

The image itself is built from `website/Dockerfile`.

## What the module stamps

One `module "console"` instantiation creates:

| Area | Resources | Notes |
|---|---|---|
| Network | VPC, public subnets (ALB + NAT only), private subnets (all tasks), S3 gateway endpoint, interface endpoints for `ecr.api`, `ecr.dkr`, `logs`, `secretsmanager`, `kms` | Tasks reach the control plane over the **public internet through NAT**; the endpoints keep image pulls, logs, and secret injection off that path |
| Public edge | ALB (HTTPS + an HTTP listener that only redirects), ACM certificate with DNS validation, Route 53 hosted zone (created or looked up), A/ALIAS records for the apex and `www` | Public ingress **is the point**: marketing, sign-in, and the Stripe webhook all arrive from anywhere |
| Data | RDS Postgres 16 (encrypted, TLS-forced, private, storage autoscaling) | Schema, the RLS-bound `convoy_website_app` role, and every policy come from `npm run db:migrate`, not Terraform |
| Compute | ECS cluster + Fargate services: `web` (behind the ALB, `npm run start`) and `notifier` (no ingress, `npm run notifier`), plus a registered **migrate task definition** (no service — `run-migrations.sh` `RunTask`s it) | All three are one image under different commands |
| Registry | One ECR repository `convoy-console-<stack>/website`, **immutable tags**, scan-on-push, 25-image lifecycle | Every push needs a new tag, including the first `bootstrap` one |
| Secrets | Session signing key (generated), Postgres master credentials, Postgres app-role credentials — all in Secrets Manager under the console KMS key | No secret value is ever written into code, tfvars, or a script |
| IAM | One execution role (pull, log, inject secrets) and three task roles (web, notifier, migrate), each with **zero AWS data access plus an explicit deny** | The console holds no domain data, so no task role can reach a data store |
| Observability | KMS-encrypted CloudWatch log groups `/convoy/console/<stack>/{web,notifier,migrate}`, Container Insights | |

## Required inputs

Everything else has documented defaults — see
`terraform/modules/console-stack/variables.tf` for the full surface.

| Variable | Meaning |
|---|---|
| `stack_name` | Resource prefix `convoy-console-<stack_name>`; e.g. `prod`. Capped at 12 characters by the load balancer name limit |
| `environment` | Environment label; tags every resource and rides into the tasks |
| `domain_name` | Apex domain. The certificate covers it and `www.<domain>`, and both get alias records |
| `control_plane_url` | Base HTTPS URL of the runtime control plane the console calls |
| `secret_arns` | Map of externally-managed Secrets Manager ARNs. `control_plane_token` is required; `workos_api_key`/`workos_client_id` and `stripe_secret_key`/`stripe_webhook_secret` are optional pairs |

Posture knobs worth reviewing per deployment: `create_hosted_zone`,
`db_instance_class`, `db_multi_az`, `single_nat_gateway`, `web_desired_count`,
`alb_idle_timeout`, `alb_ingress_cidrs`, `deletion_protection`.

`notifier_desired_count` defaults to 1 and should stay there. Notification
writes are idempotent on the event id and each organization's cursor is
monotonic, so a second task is safe — it just re-walks the same feed and loses
every race.

### How the URL, the certificate, and DNS fit together

One apply turns a registered domain into a working HTTPS address; no human
copies a validation record:

1. The hosted zone is either created (`create_hosted_zone = true`) or looked up
   as a data source. Either way it is where the rest of the records land.
2. ACM issues a DNS-validated certificate for `<domain>` and `www.<domain>`.
   Terraform writes the validation CNAMEs into that zone and
   `aws_acm_certificate_validation` blocks until AWS has issued.
3. The HTTPS listener attaches the **validated** certificate, so it is never
   pointed at one AWS has not issued yet.
4. Alias A records for the apex and `www` point at the ALB. Port 80 exists
   only to redirect; nothing is served over it.

The one ordering fact that bites: if you are creating the zone, AWS cannot
validate the certificate until the registrar's name servers are the ones
`hosted_zone_name_servers` reports. The first apply will sit at certificate
validation until that propagates. Setting the name servers first — see step 1
of the runbook — avoids the wait.

## Stamping runbook (fresh console)

Prerequisites: Terraform >= 1.6, AWS credentials for the ops account, `aws`
CLI v2, `jq`, `docker`. State backend bucket + KMS key exist in the ops
account. A runtime stack is already live and reachable at a public HTTPS
address.

1. **Own the domain.** Register it, or take over DNS for one you already own.
   If Route 53 is not yet authoritative, either create the zone by hand and
   set the registrar's name servers to it now (then stamp with
   `create_hosted_zone = false`), or stamp with `create_hosted_zone = true`,
   read `hosted_zone_name_servers`, set them at the registrar, and let the
   apply finish validating.

2. **Provision the Convoy-operated secrets** (ops account), each as a
   Secrets Manager secret holding the bare value:
   - the control-plane bearer the console presents on every domain call;
   - WorkOS API key and client id, if sign-in is going through AuthKit;
   - Stripe secret key and webhook signing secret, if billing is on.

   If any of them is encrypted with a customer-managed KMS key, that key's
   policy must allow `kms:Decrypt` for the console's execution role — this
   module cannot grant itself access to someone else's key. See "By hand"
   below.

3. **Create the console root**: copy `terraform/stacks/example/` to
   `terraform/stacks/<name>/`, uncomment and fill `backend.tf` (unique state
   key; the encrypted S3 backend is **mandatory** — the session key and both
   database passwords are generated and therefore live in state), and write
   `<name>.tfvars` from `fixtures/example.tfvars`.

4. **Apply**:
   ```sh
   terraform init
   terraform plan  -var-file=<name>.tfvars -out=stamp.tfplan
   terraform apply stamp.tfplan
   ```
   First apply takes ~20 minutes (RDS, plus certificate validation). Record
   `terraform output -json` — the deploy scripts read it.

5. **Push the first image**:
   ```sh
   STACK_NAME=<name> AWS_REGION=<region> \
     deploy/build-and-push.sh --tag bootstrap
   ```
   `bootstrap` matches the module's default `image_tag`, so both services
   start as soon as the image exists. Tags are immutable: a second
   `--tag bootstrap` is refused, by design.

6. **Initialize the database**:
   ```sh
   STACK_NAME=<name> AWS_REGION=<region> deploy/run-migrations.sh
   ```
   This runs the migrations as a one-off Fargate task on the private subnets
   and then aligns the RLS-bound role's password with the generated one. The
   database is never exposed to do it. Until this runs, both services restart
   in a loop: the schema is not there and the app-role password is wrong.

7. **Deploy the release you actually want to serve** (skip on a fresh stamp if
   `bootstrap` is that release):
   ```sh
   TAG=$(git rev-parse --short=12 HEAD)
   STACK_NAME=<name> AWS_REGION=<region> deploy/build-and-push.sh --tag "${TAG}"
   STACK_NAME=<name> AWS_REGION=<region> deploy/deploy-service.sh --tag "${TAG}"
   ```

8. **DNS check**: `dig +short <domain>` and `dig +short www.<domain>` both
   resolve to the ALB, and `curl -I http://<domain>` returns a 301 to HTTPS.
   If the apex still answers from the registrar's parking page, the name
   servers have not propagated.

9. **Smoke test — a real person can sign in and see a run**: load
   `https://<domain>` (marketing renders, no console styling missing), sign in
   through the configured provider, land in the portal, open a run whose id
   you know from the runtime, and watch the timeline stream. The last part is
   the one that exercises everything at once: session cookie, RLS-scoped
   database reads, the control-plane bearer, and SSE through both the ALB and
   the app's proxy route. Then confirm the notifier is alive — its log group
   shows a `started` line and ticks without errors.

### Verifying the security posture (part of the stamp gate)

Record the evidence in the PR that changes this module:

- **The database is unreachable from outside.** `psql` to `db_endpoint` from
  anywhere but a console task times out, and a plaintext connection attempt is
  refused by `rds.force_ssl`.
- **The running app cannot bypass RLS.** From a web task,
  `SELECT current_user` returns `convoy_website_app`, and a query issued
  without org context returns zero rows rather than every row.
- **The old development password is dead.** Connecting as
  `convoy_website_app` with the password in `0001_init.sql` fails
  authentication. If it succeeds, step 6's second half did not run.
- **No task role can reach a data store.** From a task, any `aws s3 ls` or
  `sts:AssumeRole` fails with an explicit deny.

## Day-2 operations

### Deploying a new version

```sh
export STACK_NAME=<name> AWS_REGION=<region>
TAG=$(git rev-parse --short=12 HEAD)

deploy/build-and-push.sh --tag "${TAG}"
deploy/run-migrations.sh --tag "${TAG}"     # only when the release adds migrations
deploy/deploy-service.sh --tag "${TAG}"     # web + notifier, then the migrate task def
```

Notes:
- Migrations run **before** the deploy, so they must be backward compatible
  with the version still serving traffic — for the length of one rollout, both
  are live against the same schema.
- `deploy-service.sh --service web` or `--service notifier` deploys one of
  them; the default deploys both, because they share an image and drifting
  them apart means the notifier writes notifications for a UI that no longer
  matches.
- The deployment circuit breaker rolls a failing rollout back on its own.
  Rolling back deliberately is `deploy-service.sh --tag <previous>` — the tag
  is immutable, so the previous tag is still exactly the bytes that worked.
- Terraform owns the task-definition families and the services, with
  `ignore_changes` on `task_definition`. Do not "fix" the resulting drift by
  re-applying with a new `image_tag`.

### Rotating the session signing key

Session cookies are JWTs signed with this key, so rotating it signs every
user out. That is the intended behavior after a suspected leak; it is not
something to do casually.

```sh
terraform apply -replace='module.console.random_password.session_secret' \
  -var-file=<name>.tfvars
deploy/deploy-service.sh --service web --tag <current>   # pick up the new value
```

The secret is injected at container start, so the running tasks keep the old
key until they are replaced. Until the redeploy finishes, sessions signed with
either key are in flight — expected, and over within one rollout.

### Rotating the database passwords

Same shape, with one ordering constraint: the app-role password lives in two
places, and Postgres has to learn the new one before the tasks do.

```sh
terraform apply -replace='module.console.random_password.db_app' \
  -var-file=<name>.tfvars
deploy/run-migrations.sh                   # teaches Postgres the new password
deploy/deploy-service.sh --tag <current>   # tasks pick it up
```

Between the apply and the migration run, the running tasks still hold the old
password and keep working. Reversing the order breaks sign-in until the
migration catches up.

### Scaling

- **Web**: raise `web_desired_count`. Two is the floor — the ALB needs a
  healthy target while a deploy replaces a task. Beyond that it is a request
  volume question; the tasks are stateless and hold nothing between requests.
- **Notifier**: leave it at one. More tasks do not go faster, they re-walk the
  same feed.
- **Database**: `db_instance_class` first, then `db_multi_az` for production.
  Storage grows on its own up to `db_max_allocated_storage`.
- **Availability**: `single_nat_gateway = false` for production. With one NAT,
  losing its AZ cuts every control-plane call and the notifier's feed off from
  the internet.

### Tearing a stamp down

Only sane for a stamp stood up with `deletion_protection = false`. That flag
is what governs whether the stack can be removed at all, and it covers three
resources that each refuse deletion by default:

| Resource | With protection on | With it off |
|---|---|---|
| Database | refuses deletion, keeps a final snapshot | deletes, no snapshot |
| ECR repository | refuses deletion while it holds images | deletes with its images |
| Secrets | 30-day recovery window, name stays taken | deleted immediately |

```sh
cd terraform/stacks/<name>
terraform destroy -var-file=<name>.tfvars
```

The secret recovery window is the one that surprises people. Secret names are
derived from the stack name, so with a window in force a destroyed stamp
blocks its own replacement: the next apply asks for a name that still exists,
pending deletion, and fails. Seven days is the shortest window AWS accepts,
so a stamp that needs to be re-creatable has to run at zero.

Flipping `deletion_protection` from true to false is itself an apply, and it
has to land **before** the destroy. Discovering this halfway through a
teardown means an apply, then the destroy again.

Two things survive on purpose and are not the module's to remove: the Route 53
hosted zone when `create_hosted_zone = false` (it is read as a data source),
and every secret passed in through `secret_arns`, which belongs to whoever
issued it.

### Reading the web service's logs

Two lines look like problems and are not:

  serves from the full build so that web, notifier, and migrations can share
  one artifact; pages and static assets are served correctly, which the smoke
  test in step 9 confirms.
- A burst of database errors from the notifier immediately after a stamp. It
  ticks every two seconds and the schema does not exist until step 6 runs.
  They should stop the moment migrations land; if they do not, the app-role
  password sync is what to check first.

## CI lint lane (no AWS credentials required)

```sh
terraform fmt -check -recursive website/infra/terraform
(cd website/infra/terraform/modules/console-stack && terraform init -backend=false && terraform validate)
(cd website/infra/terraform/stacks/example      && terraform init -backend=false && terraform validate)
docker build -t convoy-console:ci website/
```

With read-only AWS credentials the lane can additionally run
`terraform plan -var-file=fixtures/example.tfvars` in `stacks/example`.
The committed `.terraform.lock.hcl` files pin providers for `linux_amd64`;
run `terraform providers lock -platform=darwin_arm64 ...` to extend them for
other operator platforms.

## Not stamped here

Convoy-operated or owned elsewhere, wired in as variables:

- **WorkOS.** The AuthKit environment, its redirect URIs, and the API key and
  client id secrets. Without them the app falls back to its clearly-labeled
  local sign-in provider, which is not a production posture.
- **Stripe.** The account, the products and prices, the webhook endpoint
  pointing at `https://<domain>/api/webhooks/stripe`, and the `org_id`
  metadata Convoy staff set on each customer. Billing is invoice-first: the
  console never mutates plan or status, it only receives them by webhook.
  Without the Stripe secrets the billing surfaces stay dark and that endpoint
  answers 503.
- **The runtime stack.** The control plane the console calls is a separate
  `terraform apply` from `agent-runtime/infra/`, in its own VPC, with its own
  ALB and certificate. This module only needs its public URL and a bearer.
- **Per-organization control-plane routing.** `control_plane_url` and
  `secret_arns["control_plane_token"]` are single stack-wide values because
  every organization is served by one shared runtime stack today. When
  organizations get dedicated stacks, the console has to resolve endpoint and
  bearer per organization — that is a lookup at request time, not a Terraform
  variable, and both of these change shape when it lands.
- **The Terraform state backend.** The state bucket and its KMS key belong to
  the ops account and predate any console.

### By hand

Two things an operator has to do that no apply covers:

1. **Name servers at the registrar** when the zone is created here (step 1).
   Nothing resolves and no certificate validates until they point at the
   hosted zone.
2. **KMS grants on externally-managed secrets.** If a WorkOS, Stripe, or
   control-plane-token secret is encrypted under a customer-managed key, add
   `kms:Decrypt` for `convoy-console-<stack>-task-execution` to that key's
   policy. Secrets under the AWS-managed Secrets Manager key need nothing.
   The symptom of forgetting is tasks that fail to start with a
   `ResourceInitializationError` naming the secret.

## Security invariants enforced here

| Invariant | Where |
|---|---|
| The app can never bypass row-level security | services connect as `convoy_website_app` from the app-role secret; the master credentials go only to the migrate task |
| The shipped development password never reaches production | `run-migrations.sh` follows every migration with `infra/db/sync-app-role.mjs`, using a generated password injected from Secrets Manager |
| Database traffic is encrypted and the server is verified | `rds.force_ssl` on the parameter group; generated DSNs use `sslmode=verify-full` against the RDS trust anchor baked into the image |
| The database has no public path | private subnets, no public accessibility, ingress only from the web and task security groups; migrations run inside the VPC |
| No console credential is readable by application code | secrets are injected by the execution role at container start; no task role can call `secretsmanager` at all |
| The console holds no domain data | task roles have zero grants plus an explicit deny on S3, KMS, Secrets Manager, RDS, and `sts:AssumeRole` |
| A deployed tag always means the same bytes | ECR tag immutability; rollback is naming an older tag |
| No secret value in code | every value is either generated into Secrets Manager or referenced by ARN; `fixtures/example.tfvars` contains only dummy ARNs |
