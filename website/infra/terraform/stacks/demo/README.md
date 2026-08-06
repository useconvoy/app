# The demo console — one Lightsail box

The console used to be stamped the way it should be run: a VPC, a NAT gateway,
five interface endpoints, an ALB with an ACM certificate, an ECS cluster, and
RDS. That was roughly **$200/month**, and more than half of it was plumbing
rather than anything serving a request. Those files were removed so there is
one obvious way to deploy; they remain in git history at `4f1919a`.

This stack serves the same image to the same domain for roughly **$13/month**
by putting everything on one virtual machine.

| | ECS stack (`4f1919a`) | `stacks/demo` |
|---|---|---|
| Compute | ECS Fargate, 2 web + 1 notifier | 2 containers on one Lightsail instance |
| Database | RDS Postgres 16, managed backups | Postgres container on the instance disk |
| TLS | ACM certificate on an ALB | Caddy, automatic Let's Encrypt |
| Egress | NAT gateway + 5 interface endpoints | The instance's own address |
| Secrets | Injected per task from Secrets Manager | Fetched once at boot into a root-owned file |
| **Monthly** | **~$200** | **~$13** |

Cost is the instance ($12 for `small_3_0`, 2 GB), the Route 53 zone ($0.50),
and pennies of ECR storage. The static IP is free while attached, and Lightsail
bundles transfer into the price. Nothing here is free-tier dependent — the
account is well past its twelve months, so a "free tier" deployment was never
actually on the table; this is just genuinely cheap.

## What it gives up

Worth reading before this serves anyone who is not you.

- **No managed backups and no point-in-time recovery.** Postgres writes to a
  Docker volume on the instance disk. Losing the instance loses the data.
  Lightsail snapshots are the cheap mitigation and are not wired up here.
- **The database connection is not encrypted.** The ECS stack set
  `rds.force_ssl` and shipped an RDS trust bundle in the image. Here Postgres
  is reachable only over the compose bridge network on the same host, never
  leaving the box — different protection, not the same one.
- **An IAM access key lives on the instance.** Lightsail instances cannot
  carry an instance profile the way EC2 can, so the box authenticates with a
  key written to `/root/.aws/credentials` (mode 0600). It is scoped to pulling
  one ECR repository and reading one secret, with an explicit `Deny` on
  everything else. That is the single largest posture difference from the ECS
  stack, where no long-lived key existed anywhere.
- **The runtime environment sits on disk** at `/opt/convoy/.env` (mode 0600,
  root). On ECS these values only ever exist in a task's memory.
- **One instance means downtime is the normal case.** A reboot, a bad deploy,
  or a full disk takes the console down. There is no second target and nothing
  to fail over to.
- **Some of the old security gates have no analogue.** The "no task role can
  reach a data store" check is meaningless without task roles, and the RLS
  checks run against a local Postgres rather than RDS.

The one thing it does *not* give up: the app runs the same image, the same
migrations, and the same `convoy_website_app` RLS-bound role, with the
development password from `0001_init.sql` retired at boot in exactly the order
the ECS migration task used. See "How the database works" in `infra/README.md`
— that mechanism is hosting-independent and unchanged.

## Stamping

Prerequisites: the toolchain from `deploy/bootstrap-toolchain.sh`, credentials
for the ops account, and the Route 53 zone for the domain already present
(this stack looks the zone up, it does not create it).

```sh
cd website/infra/terraform/stacks/demo
cp demo.tfvars.example demo.tfvars   # then fill it in
terraform init -plugin-dir="$HOME/.convoy-tf-mirror"
terraform plan -var-file=demo.tfvars -out=stamp.tfplan
terraform apply stamp.tfplan
```

The apply creates the ECR repository but cannot put an image in it, so push
one before the instance is useful. Cloud-init waits up to 30 minutes for the
tag to appear before it migrates, so the order below works without
re-applying; a push that takes longer than that needs the instance replaced
(`terraform apply -replace=aws_lightsail_instance.console`, plus
`-replace` on `aws_lightsail_static_ip_attachment.console` and
`aws_lightsail_instance_public_ports.console` so the static IP and the port
rules survive the new box):

```sh
export STACK_NAME=demo AWS_REGION=us-west-2
../../../deploy/build-and-push.sh --tag bootstrap
```

Then watch the box assemble itself:

```sh
ssh ubuntu@$(terraform output -raw static_ip) sudo tail -f /var/log/convoy-bootstrap.log
```

Cloud-init installs Docker, adds 2 GB of swap (the bundles ship with none, and
a Next.js build plus Postgres will otherwise brush the ceiling), fetches the
runtime secret, writes the compose project, runs the migrations *and* the app
role sync, and starts everything.

Certificate issuance needs the A records to resolve to the instance first.
Terraform writes them in the same apply, so Caddy's first attempt may lose the
race; it retries with backoff and needs no help.

## Verifying

```sh
dig +short deployconvoy.com          # the static IP
curl -I http://deployconvoy.com      # 308 to HTTPS, from Caddy
curl -I https://deployconvoy.com     # 200, valid Let's Encrypt certificate
```

Then sign in through AuthKit. The run-timeline part of the smoke test cannot
pass — `control_plane_url` points at a placeholder, so run, plan, and workspace
surfaces stay dark by design.

## When the box comes up empty

`user_data` is not run the way it looks. Lightsail prepends its own
initialization to whatever it is given and executes the combination as one
cloud-init script, so the bootstrap's `#!/usr/bin/env bash` is not on line 1
and the shebang is ignored: cloud-init runs the file under `/bin/sh`, which is
dash on Ubuntu. That is why `cloud-init.sh.tftpl` is POSIX sh down to the
heredoc and only then hands off to bash. **Anything bash-only added above that
heredoc — `pipefail`, `[[`, arrays, `>(...)` — will fail at run time, not at
`terraform validate`.** Reported line numbers are offset by the preamble, so
"line 25" meant line 10 of this file.

Check it before deploying, using the same shell cloud-init will:

```sh
dash -n rendered-user-data.sh    # parses
dash rendered-user-data.sh       # actually runs it; parsing alone misses
                                 # `set -o pipefail`, which is a run-time error
```

If the console never answers, these three read the box's state from the AWS
side, without needing SSH:

```sh
# Did the instance ever authenticate to AWS? Zero events means the bootstrap
# died before the Secrets Manager fetch.
aws cloudtrail lookup-events --region us-west-2 \
  --lookup-attributes AttributeKey=Username,AttributeValue=convoy-console-demo-instance

# A working boot pulls ~500 MB of packages plus the image. Single-digit KB per
# five minutes means the script never got to apt.
aws lightsail get-instance-metric-data --instance-name convoy-console-demo \
  --region us-west-2 --metric-name NetworkIn --period 300 --unit Bytes \
  --statistics Sum \
  --start-time "$(date -u -d '30 minutes ago' +%Y-%m-%dT%H:%M:%SZ)" \
  --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Set once the instance pulls the console image.
aws ecr describe-images --repository-name convoy-console-demo/website \
  --region us-west-2 --image-ids imageTag=bootstrap \
  --query 'imageDetails[0].lastRecordedPullTime'
```

On the box itself, `sudo cloud-init status --long` and
`/var/log/cloud-init-output.log` carry the failure;
`/var/log/convoy-bootstrap.log` only exists once the bash half starts, so its
absence localizes the fault to the wrapper.

## Deploying a new version

`.github/workflows/deploy-console.yml` does this on every push to `main` that
touches application code. Infrastructure and prose are excluded — they do not
change the image — so a Terraform-only commit does not trigger a release.

**Migrations never run on an automatic deploy.** Postgres is a container on the
instance disk with no managed backups and no PITR, so a bad migration arriving
with a merge would be unrecoverable. When a release needs them, run the
workflow by hand from the Actions tab with `run_migrations` checked.

### One-time setup

The stack creates the role; the repository needs to be told about it. Three
repository *variables* and one *secret*:

```sh
terraform output -raw github_deploy_role_arn   # -> AWS_DEPLOY_ROLE_ARN
terraform output -raw static_ip                # -> DEPLOY_HOST
ssh-keyscan "$(terraform output -raw static_ip)"  # -> DEPLOY_HOST_KEY
```

`DEPLOY_SSH_KEY` is the secret: a private key whose public half is authorized
for `ubuntu@` on the instance. `DEPLOY_HOST_KEY` is optional — without it the
workflow accepts the host key unverified and says so in an annotation, which
is worth avoiding.

No AWS access key is involved. The workflow assumes the deploy role through
GitHub's OIDC provider, and the trust policy pins the repository *and* the
branch, so a run from a fork or a feature branch cannot assume it.

### By hand

The same two steps, when you need them directly:

```sh
TAG=$(git rev-parse --short=12 HEAD)
STACK_NAME=demo AWS_REGION=us-west-2 ../../../deploy/build-and-push.sh --tag "${TAG}"

ssh ubuntu@$(terraform output -raw static_ip) TAG="${TAG}" bash -euo pipefail -s <<'REMOTE'
cd /opt/convoy
# Anchored to the console's own image lines. A blanket
# `sed "s|:[^:]*$|:${TAG}|"` rewrites every image line in the file, so
# postgres:16-alpine and caddy:2-alpine become postgres:<sha> and
# caddy:<sha> — tags that do not exist — and the next pull fails.
sudo sed -i -E "s#(image: [0-9]+\.dkr\.ecr\.[^:]*/website):.*#\1:${TAG}#" compose.yaml
sudo docker compose pull && sudo docker compose up -d
REMOTE
```

Migrations, when a release adds them, run both halves in the same order the
ECS migration task used — schema first, then the app-role password sync:

```sh
sudo docker compose run --rm -T web sh -c \
  'npm run db:migrate && node infra/db/sync-app-role.mjs'
```

## The road back to a production stack

This stack is a detour, not a destination. The ECS stack it replaced is not
lost — it is in git history at `4f1919a`, `terraform fmt` and `validate` clean
as of that commit:

```sh
git show 4f1919a:website/infra/terraform/stacks/prod/main.tf
git checkout 4f1919a -- website/infra/terraform/modules/console-stack \
                        website/infra/terraform/stacks/prod
```

Restoring it is one command; whether it is the right starting point depends on
how far the app has moved by then. Either way, what makes the move cheap is
what the two share: the same image, the same migrations, and the same
environment-variable contract.

1. Restore or rewrite the production stack. It uses a different state key, so
   both can exist at once and the demo keeps serving during the cutover.
2. Dump the data: `pg_dump` from the compose Postgres.
3. Load the dump into RDS through the migrate task.
4. Split this stack's single runtime secret into the three separate secrets
   the production stack expects, and put their ARNs in its tfvars.
5. Re-point the A records from the static IP to the ALB alias.
6. `terraform destroy` here. Everything is built to allow it — `force_delete`
   on the repository and a zero-day recovery window on the secret — so the
   name is free for a re-stamp immediately.

The cutover is a few hours, and step 5 is the only one users notice.
