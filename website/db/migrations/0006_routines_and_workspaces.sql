-- Real per-organization routines and workspaces, plus the append-only
-- test-score history behind the evaluation surfaces.
--
-- Until now these shapes lived in fixture modules inside the app, so every
-- organization saw the same demo world. From here on a fresh organization
-- starts empty and fills these tables through the catalog install flow and
-- the workspace screens. The website DB still never mirrors domain state:
-- runs, plans, steps, and checkpoints stay behind the control plane. What
-- lands here are the console's own routing facts: what a routine is called,
-- which systems it touches, where it runs, and how its runs have scored.

-- Workspaces: where routines run. The systems column holds the granted
-- system list (id, display name, scope, stand-in note) as jsonb rather than
-- a child table because grants are read and written only as a whole set,
-- always with their workspace, and never queried across workspaces.
-- versions works the same way: an append-only jsonb list of
-- (version, note, createdAt) entries the workspace detail renders. The
-- environment bindings are references into the runtime's environment
-- registry; the website stores the ids and nothing behind them.
CREATE TABLE workspaces (
  id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                     uuid NOT NULL REFERENCES organizations(id),
  name                       text NOT NULL,
  purpose                    text NOT NULL DEFAULT '',
  environment_id             text NOT NULL,
  rehearsal_environment_id   text NOT NULL,
  systems                    jsonb NOT NULL DEFAULT '[]',
  clock_mode                 text NOT NULL DEFAULT 'wall' CHECK (clock_mode IN ('wall', 'virtual')),
  versions                   jsonb NOT NULL DEFAULT '[]',
  created_at                 timestamptz NOT NULL DEFAULT now()
);

-- Routines: the jobs an organization has handed over. systems is a flat
-- text[] of system ids; the grant details (scope, stand-ins) live on the
-- workspace the routine is bound to. budget_cap_usd rides every run
-- creation, so it is numeric, never a display string. plan_steps holds the
-- plain-language step sentences the routine page renders; the executable
-- plan template belongs to the runtime, and an installed routine may carry
-- no steps until its first run reveals them. source_entry_id and
-- source_version record the catalog install that produced the row and pin
-- the installed snapshot, which is what "update available" compares
-- against. workspace_id may be null for a routine whose workspace was
-- deleted; the routine survives and its detail page says so.
CREATE TABLE routines (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          uuid NOT NULL REFERENCES organizations(id),
  workspace_id    uuid REFERENCES workspaces(id) ON DELETE SET NULL,
  name            text NOT NULL,
  descriptor      text NOT NULL DEFAULT '',
  systems         text[] NOT NULL DEFAULT '{}',
  budget_cap_usd  numeric(12,2) NOT NULL DEFAULT 0 CHECK (budget_cap_usd >= 0),
  plan_steps      jsonb NOT NULL DEFAULT '[]',
  -- Plain schedule text ("Mondays at 9am"); the scheduler that acts on it
  -- is runtime-side. Null means the routine runs on demand only.
  schedule_description text,
  source_entry_id uuid REFERENCES catalog_entries(id),
  source_version  int,
  created_by      uuid REFERENCES users(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Test scores: one narrow row per scored run, append-only. This table
-- grows without bound as routines run and rehearse, so it stays narrow on
-- purpose: a 0..100 score, a run reference, and a stamp. Anything wide
-- (scorecards, criteria, trajectories) belongs to the evaluation service
-- when it exists, never to this table. The identity key is half the width
-- of a uuid and preserves insert order for stable pagination later.
CREATE TABLE routine_eval_scores (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  org_id      uuid NOT NULL REFERENCES organizations(id),
  routine_id  uuid NOT NULL REFERENCES routines(id) ON DELETE CASCADE,
  run_ref     text NOT NULL,
  score       int NOT NULL CHECK (score BETWEEN 0 AND 100),
  recorded_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_workspaces_org ON workspaces (org_id);
CREATE INDEX idx_routines_org ON routines (org_id);
-- The workspace cards count "used by N routines" through this.
CREATE INDEX idx_routines_workspace ON routines (workspace_id) WHERE workspace_id IS NOT NULL;
-- One installed row per catalog entry per organization: reinstalling an
-- entry re-pins the existing routine instead of stacking duplicates.
-- Routines created outside the catalog carry no source_entry_id and are
-- unconstrained.
CREATE UNIQUE INDEX uq_routines_source_entry
  ON routines (org_id, source_entry_id) WHERE source_entry_id IS NOT NULL;
-- The trend and scored-run reads are always "this routine, newest first";
-- this index serves both without touching rows of other routines.
CREATE INDEX idx_eval_scores_routine ON routine_eval_scores (routine_id, recorded_at DESC);

ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE routines ENABLE ROW LEVEL SECURITY;
ALTER TABLE routine_eval_scores ENABLE ROW LEVEL SECURITY;

-- All three tables are plain org-scoped facts with no cross-org visibility
-- of any kind, so they take the same org_scoped policy shape as teams and
-- invites in 0001: rows exist only inside the active org context, for
-- reads and writes alike.
CREATE POLICY org_scoped ON workspaces FOR ALL USING (org_id = app_org_id())
  WITH CHECK (org_id = app_org_id());
CREATE POLICY org_scoped ON routines FOR ALL USING (org_id = app_org_id())
  WITH CHECK (org_id = app_org_id());
CREATE POLICY org_scoped ON routine_eval_scores FOR ALL USING (org_id = app_org_id())
  WITH CHECK (org_id = app_org_id());

-- 0001's default privileges cover tables only. The identity column above
-- draws from a sequence the app role must be able to advance, so sequences
-- get their grant here, once, for this and future migrations.
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO convoy_website_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO convoy_website_app;
