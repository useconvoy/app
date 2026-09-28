# AWS CPU staging deployment and recovery

Status: configuration and local qualification; **not applied to AWS**. This is
one synthetic-data installation of the supported robot lifecycle. It cannot
host multiple customers securely yet. Legacy installation-wide APIs still need
the tenancy changes described in
[decision 003](decisions/003-service-hosting.md).

## What runs where

| Process | CPU / memory | State and access |
| --- | --- | --- |
| Web / console | 0.25 vCPU / 0.5 GiB | Caller session only; HTTPS to API through management ALB |
| API | 0.5 vCPU / 1 GiB | PostgreSQL runtime role, bootstrap login and execution signer |
| Scheduler | 0.25 vCPU / 0.5 GiB | PostgreSQL runtime role; existing maintenance and lease loop |
| Evaluation service | 0.25 vCPU / 0.5 GiB | PostgreSQL runtime role; ordinary mission orchestration; no model code |
| Scripted CPU inference | 1 vCPU / 2 GiB | Pinned MetaWorld runtime; public action keys/probe credential; no DB access |
| One-off bootstrap / migrate | 0.25 vCPU / 0.5 GiB each | Administrative / migration role; no permanent service |
| PostgreSQL | db.t4g.small, 20 GiB gp3, single AZ | Fleet/auth/deployment/mission/evaluation metadata; seven-day automated backups |

All service counts are one, with no autoscaling and no overlapping rolling
replacement. Deployments deliberately have downtime; drain active missions
before changing an image or key. One NAT and private task placement in the first
AZ limit staging idle costs and availability. Public ALBs and RDS subnet groups
span two AZs to meet AWS requirements, but this is not application redundancy.
The required CPU architecture must match every provided digest; the local image
qualification used **ARM64**. AMD64 has not been qualified by the blocked CI run.

The robot coordinator and its SQLite journal remain on the local simulator or
future Jetson edge machine. This Terraform does not create a robot, move its
journal into Fargate, deploy a model to a Jetson, or claim remote control timing.
The CPU reference is scripted, not the learned visual policy. A compatible
learned/GPU worker needs its own image, sizing and evidence.

## Network and data boundaries

Three DNS names are required: console, management API and inference. Two ACM
certificates in the selected region cover the console/API and inference names.
Two independent public ALBs listen on HTTPS 443 only. The robot initiates
outbound HTTPS; no robot-side inbound port is required. Web forwards its user's
session to the API's HTTPS DNS name; it holds no operator password.

TLS terminates at the ALBs. Their private HTTP target connections are admitted
only from the appropriate ALB security group. This is **not end-to-end TLS**.
PostgreSQL independently enforces TLS; the wrappers verify its hostname and
public RDS CA. Do not disable verification to diagnose connectivity. Tasks can
make outbound HTTPS through one NAT for ECR, secrets, logs and the web-to-API
path. ECR layer traffic to S3 uses the free gateway endpoint. There is no DB
public endpoint and inference/web have no DB security-group rule. There are no
interface endpoints, service mesh or GPU hosts in this configuration.

Fargate files are disposable. The AWS API wrapper explicitly returns 404 for
unavailable legacy endpoints: text chat, model catalog/releases, artifact
uploads/downloads, rollouts/schedules/operations, admin user/token changes,
legacy agent polling and API docs. Available endpoints are auth login/logout/me,
project/robot/application/deployment/mission/episode/evaluation lifecycle,
enrollment, read-only device inventory and coordinator desired/claim/report.
Core authentication and project authorization still apply to every admitted
request. Do not add a route to the wrapper until its persistence is qualified.
There is no S3 artifact integration, EFS SQLite mount or durable media capture
claim. Health and lifecycle metadata survive task replacement in PostgreSQL;
legacy local files do not. Existing simulated device records may appear offline
because this deployment uses the mission coordinator rather than the old agent
heartbeat/operation loop.

## Required inputs before spending

The operator must supply an approved AWS account and role, region/two AZs,
spending threshold/notification recipient, DNS zone/certificate ARNs, registry
repositories and qualified image digests/architecture, and an existing encrypted
versioned state bucket with locking. None has been inferred from an older demo.
The [US East cost example](aws-cpu-staging-cost.md) is a planning scenario, not
the user's selected region or spending authorization.

Use a dedicated staging account if available. The deployment role needs the
resource-management permissions in the reviewed plan and scoped `iam:PassRole`
for these ECS task/execution roles; the image publisher needs push access only
to the three selected repositories. The maintenance operator additionally needs
ECS RunTask/DescribeTasks/ListTasks/DescribeServices/ListServices and access to
the corresponding logs. Prefer short-lived assumed roles/OIDC. No permanent
AWS credential, secret value or `.tfvars` belongs in Git.

Verify the pinned PostgreSQL minor and db.t4g.small are offered in the selected
AZ/region using `describe-db-engine-versions` / `describe-orderable-db-instance-options`
before a real plan. RDS documents PostgreSQL 17.11 support; local PostgreSQL
qualification used 17.11, but that does not prove account/region availability.
[Supported RDS versions](https://docs.aws.amazon.com/AmazonRDS/latest/PostgreSQLReleaseNotes/postgresql-versions.html)

## Build and publish the exact images

Build the existing API, standalone website and scripted reference images using
[local services](local-services.md). The API must contain the evaluation module
and migration `0002_evaluations` when `enable_evaluations=true`. Run the real local
service and evaluation acceptance first. Record base image digests and source
revision; tags are only local build handles.

Create the two small AWS derivative images from approved **registry digests**:

```sh
docker build -f infra/aws-v1/api.Dockerfile \
  --build-arg BASE_IMAGE="$QUALIFIED_API_DIGEST" -t "$AWS_API_BUILD_TAG" .
docker build -f infra/aws-v1/inference.Dockerfile \
  --build-arg BASE_IMAGE="$QUALIFIED_REFERENCE_DIGEST" -t "$AWS_INFERENCE_BUILD_TAG" .
```

The website uses its already qualified image unchanged. Run the wrapper test
from `infra/aws-v1/README.md`, scan/publish the reviewed images into the approved
private ECR repositories, and obtain their immutable `repository@sha256:...`
references. Confirm the image manifest architecture and preserve previous
qualified digests for rollback. Set these AWS derivative digests in `images`.
Terraform never builds or pushes an image. Tag immutability/retention and CI
publish roles are registry/account prerequisites, not resources created here.

## Initialize stopped infrastructure, then migrate

1. Copy `infra/aws-v1/example.tfvars` to a private `.tfvars`, replace every
   placeholder, select `cpu_architecture`, and keep `services_enabled=false`.
   Choose a unique final snapshot identifier. The placeholder example cannot
   establish valid AWS account/DNS/image access.
2. Create private `backend.hcl` for a pre-existing encrypted/versioned S3 bucket:

   ```hcl
   bucket       = "APPROVED_STATE_BUCKET"
   key          = "convoy-v1-staging/terraform.tfstate"
   region       = "APPROVED_STATE_REGION"
   encrypt      = true
   use_lockfile = true
   ```

   Limit state/key access and keep audit/version history. Then run `terraform
   init -backend-config=backend.hcl` in `infra/aws-v1`, plan with the private
   variables and save/review that exact plan. **Only after the account, region,
   cost and access are agreed should the reviewed plan be applied.**
3. The first apply creates network/database/task definitions and services with
   desired count zero. Populate the six secret containers shown by `terraform
   output -json secret_arns`. `runtime-db`, `migration-db`, `admin` and `probe`
   receive independent strong random plain strings; the probe requires at least
   32 characters. The API bootstrap email is `operator@<console domain>`.
   `execution-signing` receives the complete private JSON document described in
   [execution signing](execution-signing.md); `action-verification` receives only
   its matching public action document. Generate them with the shared
   `infra/runtime/execution_keys.py initialize` helper in an environment containing
   `convoy-contracts[signing]`, using two private directories and an installation-
   specific issuer. The helper creates keys once and refuses overwrites.
   Use the secret console or CLI file input, never key material in command-line
   literals or Terraform variables. RDS retains its own separate master secret.
   Only the API execution role can fetch `execution-signing`; only inference can
   fetch `action-verification`. Neither application task role has AWS API access.
   At task startup each wrapper consumes its one injected JSON document into a
   validated mode-0600 file in a private temporary directory, before serving.
4. From the repository root with Terraform/AWS CLI on PATH, explicitly run:

   ```sh
   python3 infra/aws-v1/run_task.py bootstrap --region "$APPROVED_REGION"
   python3 infra/aws-v1/run_task.py migrate --region "$APPROVED_REGION"
   ```

   Each helper checks that services and other tasks are stopped, starts exactly
   one ECS task and requires its container exit code 0. Bootstrap verifies existing roles are unprivileged, creates
   non-superuser `convoy_migrator` and `convoy_app` roles; only the migration role
   can create schema. Default grants make new migrated tables usable by the
   runtime role. No secret is fetched through Terraform or printed by the helper.
   The operator must serialize maintenance commands; the CLI precheck is not a
   distributed maintenance lock. Inspect the original task before any retry.
5. Set `services_enabled=true`, review/apply the updated plan, and wait for
   healthy ALB targets and stable ECS services. Do not ignore a failed migration
   or stamp a schema. A broken deployment remains diagnosable in per-process
   CloudWatch logs with 14-day retention.

Secret injection occurs at task start; changing a secret version does not update
running processes. Rotate DB secrets with services stopped, rerun bootstrap,
then restart the affected tasks. Execution-key rotation also drains/cancels
missions before task replacement: publish overlapping public keys and restart
inference first, then update/restart the API signer, retiring old keys after the
original grant lifetimes. This single-replica template has downtime and loses
in-memory sessions on replacement; it does not implement live secret refresh or
transparent session migration. Never treat a secret-store update alone as
revocation in running tasks. [ECS secret injection](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/secrets-envvar-secrets-manager.html)

## Hosted acceptance and subsequent releases

Before reporting the deployment as working, verify trusted HTTPS to all three
names, runtime RDS TLS via `pg_stat_ssl`, API health backend/revision, and the
scheduler heartbeat. Confirm that web and inference have no DB credentials or
network path to PostgreSQL. Sign in through the actual console, enroll a fresh
simulator on a controlled Linux machine, and use its normal coordinator with
the inference HTTPS URL/probe credential. Run one fixed-seed mission and compare
its immutable episode to the device's actual applied-action journal. Run a
candidate evaluation, compare fixed cases, verify promotion admission and
cancel/restart recovery. Check the wrapper's unavailable endpoints return 404.
Do not use the laptop-only pipeline helper as evidence that AWS deployed.

For an image/schema update, cancel/drain missions first and inspect unknown
outcomes without replaying them. Set `services_enabled=false`, apply and wait
for all tasks to stop. Publish qualified new digests, run the new migration
task, then enable services. App rollback is allowed only to a digest compatible
with the current schema; database migrations are not automatically reversed.
The coordinator/journal stays on its edge machine throughout.

## Restore and teardown

RDS maintains seven days of automated backups. A restore produces a separate
instance/endpoint: do not overwrite live state or reconnect devices immediately.
Start from the existing [PostgreSQL recovery boundary](postgresql-development.md),
keep services stopped, quarantine the restored installation, invalidate old
device grants/credentials and rotate signing authority as required by that
runbook, then qualify schema/roles and reconciliation against the restored copy.
The application does not automate RDS restore/quarantine. A successful backup
setting is not a passed restore drill; record one before any customer pilot.

To stop compute, drain/cancel missions and apply `services_enabled=false`.
This keeps the database, ALBs, NAT, IPv4s, secrets and storage billed. To tear down,
inspect active missions, take any required evidence/snapshot, review `terraform
plan -destroy` and apply it through the approved role. The retained final RDS
snapshot, pre-existing ECR images/state bucket/certificates/DNS zone, and secrets
pending their seven-day recovery window are outside immediate resource removal.
A final snapshot name must be unique; change it before a repeated teardown.
Snapshots and registry/state storage need deliberate retention/deletion decisions.
RDS stop is temporary and automatically ends after seven days; it is not a
permanent cost switch. [RDS stop/start](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_StopInstance.html)

No cloud apply, image push, real AWS plan, DNS change, budget email or cloud
secret write was performed while preparing this configuration.
