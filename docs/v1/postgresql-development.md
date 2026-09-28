# PostgreSQL development foundation

This slice runs the existing fleet API, project/application/deployment/mission
records and scheduler against a fresh PostgreSQL database. It retains SQLite as
the default for existing demos and keeps device/coordinator journals on SQLite.
It does not import an existing SQLite installation or provision cloud resources.

## Run locally

Start Docker, then run from the repository root:

```sh
docker compose -p convoy-v1-postgres -f infra/development/postgres.compose.yml up -d --wait
cd control-plane
uv sync --frozen --all-packages --python 3.12
export DATABASE_URL='postgresql+psycopg://convoy:convoy-local-only@127.0.0.1:55432/convoy_dev'
export CONVOY_DATA_DIR='./data-postgres'
uv run --frozen convoy-server migrate --dry-run
uv run --frozen convoy-server migrate
uv run --frozen convoy-server doctor
uv run --frozen convoy-server create-user operator@example.test --role admin
uv run --frozen convoy-server serve --host 127.0.0.1 --port 8080
```

`create-user` prompts for a password. The compose credentials are public,
development-only values; the database port is bound to loopback. The pinned image
is PostgreSQL 17.11 on Debian bookworm. A named volume preserves local data across
container restarts. `docker compose -p convoy-v1-postgres -f
infra/development/postgres.compose.yml down` from the repository root stops it
without deleting that volume.

Run `uv run --frozen convoy-server worker` in another terminal with the same
database and data-directory configuration when using the scheduler. API and
scheduler should share the artifact directory until the object-storage work is
implemented. See the manipulation example for the independent inference worker,
coordinator, execution secret and simulator configuration; this database change
does not start those processes.

An unset `DATABASE_URL` still uses the existing SQLite database under
`CONVOY_DATA_DIR`. Changing the URL selects a different installation; it does not
move any users, credentials, artifacts or history.

## Schema and transaction ownership

PostgreSQL schema changes use versioned Alembic revisions packaged with the
server. Revision `0001_fleet` freezes all 40 current tables and their indexes; it
does not generate DDL from whatever ORM models happen to be installed later.
The API and scheduler refuse absent or incompatible revisions. Only the explicit
`migrate` command changes the schema. Stop services before applying future
revisions. The first revision supports an empty database; it refuses an existing
unversioned schema instead of stamping it as compatible. A failed revision is
rolled back in the same transaction. Future model changes require a new revision.

Existing transactions assume one writer. PostgreSQL preserves that contract with
one database-scoped advisory transaction lock used by `write_txn` and migrations.
Each writer starts from a fresh READ COMMITTED transaction, acquires the lock,
then reads and mutates state. Commit, rollback or connection loss releases it.
This permits API/worker process coordination but **serializes writes**; it is not
a throughput or horizontal-scaling claim. Direct application writes must use
`write_txn`. Raw SQL and external tools do not automatically obey this lock.

Connections use a 5-second connection/lock/pool wait and a 30-second statement
timeout. Lock conflicts return HTTP 503 with `Retry-After: 1`. For mutations that
support idempotency, retry the complete request with the same key; the server does
not replay arbitrary partially executed transaction bodies. No idle-transaction
session-kill timeout is imposed: existing artifact upload and remote metadata
paths can keep an authenticated read transaction open while performing I/O. Those
transaction boundaries need auditing before tuning long-lived-session limits.

Integer columns use PostgreSQL BIGINT to preserve SQLite's signed 64-bit storage
range, including byte sizes, generation counters and unsigned 32-bit simulation
seeds. SQLite DDL retains its original INTEGER type and autoincrement behavior.

## Backup and restore boundary

`doctor` and `/api/health` report the database backend, actual PostgreSQL server
version, schema revision, serialized-write mode and `backup.mode=external` with
`managed_by_convoy=false`. This is not evidence that a backup service is configured.
The old SQLite backup/restore and journal-mode commands explicitly refuse this
backend. The admin backup endpoint returns 501. Scheduled SQLite backups are
skipped; ordinary retention still runs.

A future hosted environment needs managed PostgreSQL backups, a measured restore
drill, artifact recovery and a SQLite import tool if old history is required.
This PR tests quarantine behavior against PostgreSQL, not `pg_dump`/`pg_restore`,
point-in-time recovery, failover, or a cloud provider's restore implementation.

An external restore must remain isolated from API, scheduler, inference and robot
traffic until the operator has completed recovery:

1. Restore to a separate database with application services stopped. Recover the
   corresponding artifact store and check the schema with the matching server.
2. Point the recovery CLI at that database and run `convoy-server quarantine
   --reason 'external PostgreSQL restore'` before exposing any application traffic.
   This invalidates restored user/password/session/API-token/device/enrollment
   authority and pauses dispatch.
3. Rotate the execution-grant secret on API and inference workers before allowing
   execution traffic. Database quarantine alone does not invalidate signed grants
   already cached by an inference worker. Stop/reconcile outstanding executions.
4. Run `convoy-server recover-admin operator@example.test` with a new password.
   Re-enroll devices, verify artifacts and reconcile desired/observed state using
   the existing restore procedure. Explicitly lift quarantine in Settings only
   after those checks. Restored mission history is not proof that a physical
   action did or did not occur.

The CLI does not automatically quarantine a database restored by outside tools.
The operator owns this sequence until PostgreSQL restore orchestration exists.

## Qualification and limits

From `control-plane`, run the ten focused cases against the local container:

```sh
CONVOY_TEST_POSTGRES_URL='postgresql+psycopg://convoy:convoy-local-only@127.0.0.1:55432/convoy_dev' \
  uv run --frozen pytest -q server/tests/postgres
uv run --frozen pytest -q server/tests --ignore=server/tests/postgres
```

The PostgreSQL test identity needs permission to create databases. Each case
creates and drops its own randomly named `convoy_test_*` database; the configured
database is only the administrative connection endpoint. Use a dedicated local
or CI service. The new GitHub job starts the same pinned PostgreSQL image. Ordinary
SQLite jobs skip these cases unless the endpoint is explicitly configured.

The small suite reuses existing HTTP auth/enrollment and mission scenarios, then
adds fresh/unknown/unversioned migration checks, concurrent idempotency and
exclusive mission admission, rollback and scheduler fencing, bounded contention,
large artifact receipts, and credential quarantine/maintenance guards. It tests
the actual PostgreSQL server rather than a SQL mock. The agent's package and
Python 3.10 dependency contract remain unchanged.

The legacy text-chat relay still uses private node-local SQLite. Artifact bytes
still live in the configured filesystem. Neither is a shared service just because
fleet metadata uses PostgreSQL; redundant API text-chat behavior and independent
artifact storage are not qualified here. Organization membership, finer role
scoping, durable evaluation jobs/outbox, full legacy endpoint qualification and
cloud deployment remain follow-up slices of the hosted-data milestone.
