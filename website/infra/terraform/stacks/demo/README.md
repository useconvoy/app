# The demo console — one Lightsail box

`stacks/prod` stamps the console the way it should be run: a VPC, a NAT
gateway, five interface endpoints, an ALB with an ACM certificate, an ECS
cluster, and RDS. That is roughly **$200/month**, and more than half of it is
plumbing rather than anything serving a request.

This stack serves the same image to the same domain for roughly **$13/month**
by putting everything on one virtual machine.

| | `stacks/prod` | `stacks/demo` |
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
- **The database connection is not encrypted.** `stacks/prod` sets
  `rds.force_ssl` and ships an RDS trust bundle in the image. Here Postgres is
  reachable only over the compose bridge network on the same host, never
  leaving the box — different protection, not the same one.
- **An IAM access key lives on the instance.** Lightsail instances cannot
  carry an instance profile the way EC2 can, so the box authenticates with a
  key written to `/root/.aws/credentials` (mode 0600). It is scoped to pulling
  one ECR repository and reading one secret, with an explicit `Deny` on
  everything else. That is the single largest posture difference from
  `stacks/prod`, where no long-lived key exists anywhere.
- **The runtime environment sits on disk** at `/opt/convoy/.env` (mode 0600,
  root). On ECS these values only ever exist in a task's memory.
- **One instance means downtime is the normal case.** A reboot, a bad deploy,
  or a full disk takes the console down. There is no second target and nothing
  to fail over to.
- **The security gates in `infra/README.md` do not all apply.** The "no task
  role can reach a data store" check has no analogue, and the RLS checks run
  against a local Postgres rather than RDS.

The one thing it does *not* give up: the app runs the same image, the same
migrations, and the same `convoy_website_app` RLS-bound role, with the
development password from `0001_init.sql` retired at boot exactly as
`deploy/run-migrations.sh` does on ECS.

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
one before the instance is useful. The instance retries its pull, so the order
below works without re-applying:

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

Then sign in through AuthKit. As on `stacks/prod`, the run-timeline part of the
smoke test cannot pass — `control_plane_url` points at a placeholder, so run,
plan, and workspace surfaces stay dark by design.

## Deploying a new version

There is no `deploy-service.sh` equivalent. The instance holds the whole
deployment, so a release is a pull and a restart:

```sh
TAG=$(git rev-parse --short=12 HEAD)
STACK_NAME=demo AWS_REGION=us-west-2 ../../../deploy/build-and-push.sh --tag "${TAG}"

ssh ubuntu@$(terraform output -raw static_ip) '
  cd /opt/convoy &&
  sudo sed -i "s|:[^:]*$|:'"${TAG}"'|" compose.yaml &&
  sudo docker compose pull && sudo docker compose up -d
'
```

Migrations, when a release adds them, run the same way `run-migrations.sh` does
on ECS:

```sh
sudo docker compose run --rm -T web sh -c \
  'npm run db:migrate && node infra/db/sync-app-role.mjs'
```

## The road back to `stacks/prod`

This stack is a detour, not a replacement, and `stacks/prod` stays on `main`
fully valid — moving back is switching it on, not rebuilding it. What the two
share is what makes the move cheap: the same image, the same migrations, and
the same environment-variable contract.

1. Dump the data: `pg_dump` from the compose Postgres.
2. Stamp `stacks/prod` (it uses a different state key, so both can exist).
3. Load the dump into RDS through the migrate task.
4. Move the three values out of this stack's single runtime secret into the
   three separate secrets `stacks/prod` expects, and put their ARNs in
   `prod.tfvars`.
5. Re-point the A records from the static IP to the ALB alias.
6. `terraform destroy` here. Everything is built to allow it — `force_delete`
   on the repository and a zero-day recovery window on the secret — so the
   name is free for a re-stamp immediately.

The cutover is a few hours, and step 5 is the only one users notice.
