# Convoy Console — Deployment Infrastructure

Operator documentation for stamping, verifying, and deploying **the console**:
the marketing site, the portal, and the notification worker.

The console is **one shared multi-tenant deployment**, not one per customer.
It holds only identity, organizations, notifications, feedback, catalog, and
billing rows; runs, plans, checkpoints, and workspaces are read through the
runtime control plane's single authenticated edge and are never mirrored here.

Today there is exactly one way to stamp it: **`terraform/stacks/demo/`**, a
single Lightsail instance running the whole console under docker compose for
roughly **$13/month**. Its own README is the runbook; this file covers what is
true regardless of where it runs.

> **This is a demo posture, deliberately.** An ECS Fargate stack — VPC, NAT
> gateway, interface endpoints, ALB with an ACM certificate, RDS Postgres, and
> per-task secret injection — used to live beside it at roughly $200/month. It
> was removed so there is one obvious way to deploy rather than two. It is not
> gone: it is in git history at `4f1919a` under
> `terraform/modules/console-stack/` and `terraform/stacks/prod/`, and
> `git show 4f1919a:website/infra/terraform/stacks/prod/main.tf` still reads
> it. When the demo graduates, that history is the starting point.

---

## Layout

```
website/infra/
├── terraform/stacks/demo/    # the only stack — one Lightsail instance
├── deploy/
│   ├── bootstrap-toolchain.sh  # installs aws CLI + Terraform + provider mirror
│   ├── build-and-push.sh       # builds the image and pushes it to ECR
│   └── lib/common.sh
├── db/sync-app-role.mjs      # baked into the image; run at boot and per release
└── docker/rds-global-bundle.pem  # baked into the image by the Dockerfile
```

The image itself is built from `website/Dockerfile`. One image serves all three
entry points — `npm run start`, `npm run notifier`, and `npm run db:migrate` —
so there is one build per release and nothing to keep in sync.

`docker/rds-global-bundle.pem` is unused by the demo, which talks to a Postgres
container over a local bridge network rather than to RDS. The Dockerfile still
copies it, so it stays until the image stops expecting it.

---

## How the database works

This is the part that survives any change of hosting, so it is worth reading
once. Nothing about the schema, the roles, or the isolation model is specific
to Lightsail, ECS, or a laptop.

### Two roles, and why

| Role | Who uses it | What it can do |
|---|---|---|
| `convoy_website_admin` | `scripts/migrate.mjs` and `infra/db/sync-app-role.mjs` only | Owns the schema; migrations run as it |
| `convoy_website_app` | The web server and the notifier, every request | Bound by row-level security; cannot see across organizations |

The running application **never** holds the admin credential. `WEBSITE_PG_DSN`
is the app role; `WEBSITE_PG_ADMIN_DSN` is the owner and is only ever present
in a migration run.

### Migrations

`website/db/migrations/*.sql` are applied in filename order by
`scripts/migrate.mjs`, which records each applied filename in a
`schema_migrations` table and wraps every file in its own transaction — a
failing migration rolls back and stops the run rather than half-applying.
Re-running is a no-op for anything already recorded, so the step is safe to
repeat. There are four today, `0001_init.sql` through `0004_org_settings.sql`.

### The development-password handoff

`0001_init.sql` creates the app role with a fixed, in-the-repo password:

```sql
CREATE ROLE convoy_website_app LOGIN PASSWORD 'convoy_website_app';
```

That exists so a laptop stack works with no setup. A deployment must not run on
it, and migrations cannot know the generated password, so **every deploy runs
two steps in this order**:

```sh
npm run db:migrate                  # schema, roles, policies
node infra/db/sync-app-role.mjs     # ALTER ROLE to the generated password
```

The second step reads `WEBSITE_PG_APP_PASSWORD`, passes it through a
transaction-local setting so it is never concatenated into SQL or logged
verbatim, and is idempotent. **Skipping it leaves the console running on a
password published in the repository.** The demo's cloud-init runs both, in
this order, at first boot.

### Row-level security

RLS is enabled on all fifteen tables, and the policies key on two functions
that read transaction-local settings:

```sql
app_org_id()   -- current_setting('app.org_id')
app_user_id()  -- current_setting('app.user_id')
```

`src/lib/db.ts` sets both with `set_config(..., true)` — the `true` makes them
transaction-local, so nothing leaks between pooled connections — at the start
of every request transaction. A query issued without that context returns zero
rows rather than every row. That is the whole tenant-isolation mechanism, and
it lives in Postgres rather than in application `WHERE` clauses, so a missed
filter in application code is not a data leak.

Invite acceptance is the one flow that runs before an org context exists; it
sets `app.invite_token` instead and has its own narrow policies.

### Where the data actually lives

On the demo, Postgres runs as a container writing to a Docker volume on the
instance disk — **no managed backups and no point-in-time recovery**. Losing
the instance loses the data. That is an accepted demo trade-off, not an
oversight; see the demo README for what it would take to change.

---

## Stamping

See `terraform/stacks/demo/README.md`. In short:

```sh
bash deploy/bootstrap-toolchain.sh          # aws CLI, Terraform, provider mirror
cd terraform/stacks/demo
cp demo.tfvars.example demo.tfvars          # then fill it in
terraform init -plugin-dir="$HOME/.convoy-tf-mirror"
terraform plan -var-file=demo.tfvars -out=stamp.tfplan
terraform apply stamp.tfplan
```

`registry.terraform.io` is blocked in the build environment, which is why
`init` needs `-plugin-dir`. Point it at the mirror **root** — the directory
containing `registry.terraform.io/` — not one level down.

## Deploying a release

```sh
export STACK_NAME=demo AWS_REGION=us-west-2
deploy/build-and-push.sh --tag "$(git rev-parse --short=12 HEAD)"
```

Tags are immutable: a re-pushed tag is refused before the build starts, not
after, so the previous tag is always exactly the bytes that worked. Rolling the
instance onto a new tag is a pull and a restart over SSH — the demo README has
the command.

## Verifying

```sh
dig +short <domain>              # the instance's static IP
curl -I http://<domain>          # 308 to HTTPS
curl -I https://<domain>         # 200, valid Let's Encrypt certificate
```

Then sign in through the configured provider and land in the portal. The
run-timeline part of a full smoke test **cannot pass** while `control_plane_url`
points at a placeholder: run, plan, and workspace surfaces stay dark by design.
Confirm the notifier is alive with `docker compose logs notifier`.

Database checks worth running once per stamp:

- `SELECT current_user` from the web container returns `convoy_website_app`.
- A query without org context returns zero rows rather than every row.
- Connecting as `convoy_website_app` with the password in `0001_init.sql`
  **fails**. If it succeeds, the app-role sync did not run.
