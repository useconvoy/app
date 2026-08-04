-- Convoy agent-runtime projection tables.
-- tenant_id on every row + RLS active even in dedicated stacks (DESIGN.md section 16).
-- Applied idempotently: by compose initdb and by the e2e bootstrap.

CREATE TABLE IF NOT EXISTS runs (
    tenant_id   text        NOT NULL,
    run_id      text        PRIMARY KEY,
    status      text        NOT NULL,
    goal        text        NOT NULL DEFAULT '',
    plan        jsonb,
    land_report jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

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

-- Row-level security: a query without tenant context sees nothing
-- (CLAUDE.md rule 10). FORCE so even the table owner is subject to policy.
ALTER TABLE runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE runs FORCE ROW LEVEL SECURITY;
ALTER TABLE run_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_events FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS runs_tenant_isolation ON runs;
CREATE POLICY runs_tenant_isolation ON runs
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS run_events_tenant_isolation ON run_events;
CREATE POLICY run_events_tenant_isolation ON run_events
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
GRANT SELECT, INSERT, UPDATE ON runs, run_events TO convoy_app;
