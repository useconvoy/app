-- Convoy agent-runtime projection tables.
-- tenant_id on every row + RLS active even in dedicated stacks.
-- Applied idempotently: by compose initdb and by the e2e bootstrap.

CREATE TABLE IF NOT EXISTS runs (
    tenant_id           text        NOT NULL,
    run_id              text        PRIMARY KEY,
    parent_run_id       text,
    status              text        NOT NULL,
    goal                text        NOT NULL DEFAULT '',
    plan                jsonb,
    land_report         jsonb,
    budget_cap_usd      numeric,
    budget_spent_usd    numeric,
    budget_reserved_usd numeric,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- Reused volumes predating newer columns pick them up here.
ALTER TABLE runs ADD COLUMN IF NOT EXISTS budget_cap_usd      numeric;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS budget_spent_usd    numeric;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS budget_reserved_usd numeric;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS parent_run_id       text;

-- Child runs of a parent, in one indexed lookup.
CREATE INDEX IF NOT EXISTS runs_parent_idx ON runs (tenant_id, parent_run_id);

CREATE TABLE IF NOT EXISTS run_events (
    tenant_id  text        NOT NULL,
    run_id     text        NOT NULL,
    seq        bigint      NOT NULL,
    event_id   text        NOT NULL,
    type       text        NOT NULL,
    ts         timestamptz NOT NULL,
    virtual_ts timestamptz,
    sandbox    boolean     NOT NULL DEFAULT false,
    actor      text        NOT NULL,
    actor_type text        NOT NULL DEFAULT 'system',
    payload    jsonb       NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (run_id, seq)
);

CREATE INDEX IF NOT EXISTS run_events_tenant_run_idx ON run_events (tenant_id, run_id, seq);

-- Per-step console view, folded from step lifecycle events.
CREATE TABLE IF NOT EXISTS run_steps (
    tenant_id   text        NOT NULL,
    run_id      text        NOT NULL,
    step_id     text        NOT NULL,
    description text        NOT NULL DEFAULT '',
    status      text        NOT NULL,
    attempt     int         NOT NULL DEFAULT 0,
    model_used  text,
    cost_usd    numeric,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, step_id)
);

CREATE INDEX IF NOT EXISTS run_steps_tenant_run_idx ON run_steps (tenant_id, run_id, step_id);

-- One durable execution-session rollup per run. The sandbox_id is only an
-- opaque provider locator; no credential or capability URL is stored here.
CREATE TABLE IF NOT EXISTS run_execution_sessions (
    tenant_id            text        NOT NULL,
    run_id               text        PRIMARY KEY,
    environment_id       text        NOT NULL DEFAULT '',
    status               text        NOT NULL DEFAULT 'unprovisioned',
    sandbox_id           text,
    sandbox_provider     text,
    sandbox_template     text,
    generation           int         NOT NULL DEFAULT 0,
    latest_checkpoint_id text,
    latest_snapshot_ref  jsonb,
    updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS run_execution_sessions_tenant_idx
    ON run_execution_sessions (tenant_id, run_id);

-- Immutable checkpoint audit rows. Snapshot bytes live in S3; Postgres keeps
-- only the claim-checked ref and lifecycle metadata needed by operators.
CREATE TABLE IF NOT EXISTS run_checkpoints (
    tenant_id            text        NOT NULL,
    run_id               text        NOT NULL,
    checkpoint_id        text        NOT NULL,
    checkpoint_sequence  int         NOT NULL,
    reason               text        NOT NULL,
    released_sandbox_id  text,
    generation           int         NOT NULL DEFAULT 0,
    snapshot_ref         jsonb,
    created_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, checkpoint_id)
);

CREATE INDEX IF NOT EXISTS run_checkpoints_tenant_run_idx
    ON run_checkpoints (tenant_id, run_id, checkpoint_sequence);

-- Row-level security: a query without tenant context sees nothing.
-- FORCE so even the table owner is subject to policy.
ALTER TABLE runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE runs FORCE ROW LEVEL SECURITY;
ALTER TABLE run_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_events FORCE ROW LEVEL SECURITY;
ALTER TABLE run_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_steps FORCE ROW LEVEL SECURITY;
ALTER TABLE run_execution_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_execution_sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE run_checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_checkpoints FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS runs_tenant_isolation ON runs;
CREATE POLICY runs_tenant_isolation ON runs
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS run_events_tenant_isolation ON run_events;
CREATE POLICY run_events_tenant_isolation ON run_events
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS run_steps_tenant_isolation ON run_steps;
CREATE POLICY run_steps_tenant_isolation ON run_steps
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS run_execution_sessions_tenant_isolation ON run_execution_sessions;
CREATE POLICY run_execution_sessions_tenant_isolation ON run_execution_sessions
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS run_checkpoints_tenant_isolation ON run_checkpoints;
CREATE POLICY run_checkpoints_tenant_isolation ON run_checkpoints
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

-- Application role: no superuser, no BYPASSRLS - RLS applies to every query.
DO $$
BEGIN
    CREATE ROLE convoy_app LOGIN PASSWORD 'convoy_app';
EXCEPTION WHEN duplicate_object THEN
    NULL;
END $$;

GRANT USAGE ON SCHEMA public TO convoy_app;
GRANT SELECT, INSERT, UPDATE ON runs, run_events, run_steps,
    run_execution_sessions, run_checkpoints TO convoy_app;
