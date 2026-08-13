-- Routines return as their own object. A routine binds an Agent (the
-- reusable definition: goal, instructions, budget, compute profile) to a
-- Workspace (the connector grants its runs use) together with how it
-- starts: on demand, on a schedule, or from provider events. One Agent may
-- run through many routines, against different workspaces.
--
-- Migrated rows keep the agent's id as the routine id on purpose. The
-- runtime's Temporal Schedules, the registry's event rules, and the run
-- attribution recorded on historical runs are all keyed by that id, so the
-- existing wiring keeps resolving with no runtime-side re-keying. New
-- routines mint fresh ids; consumers resolve either kind through the
-- routines table first and fall back to the agents table.
--
-- agents.schedule and agents.schedule_description remain as legacy
-- columns; nothing writes them after this migration.

CREATE TABLE routines (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       uuid NOT NULL REFERENCES organizations(id),
  agent_id     uuid NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name         text NOT NULL,
  -- {"cron", "timezone", "target", "enabled"}; null starts on demand only.
  schedule     jsonb,
  created_by   uuid REFERENCES users(id),
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_routines_org ON routines (org_id, created_at);
CREATE INDEX idx_routines_agent ON routines (agent_id);
CREATE INDEX idx_routines_workspace ON routines (workspace_id);

ALTER TABLE routines ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_scoped ON routines FOR ALL
  USING (org_id = app_org_id())
  WITH CHECK (org_id = app_org_id());

INSERT INTO routines
  (id, org_id, agent_id, workspace_id, name, schedule,
   created_by, created_at, updated_at)
SELECT a.id, a.org_id, a.id, a.workspace_id, a.name, a.schedule,
       a.created_by, a.created_at, a.updated_at
  FROM agents a
 WHERE a.automation_configured;
