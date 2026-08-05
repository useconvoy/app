-- Website schema (DESIGN §3): identity/org structure, notifications,
-- feedback capture, catalog, billing, and the website-side audit trail.
-- Every org-scoped table carries RLS keyed to the per-transaction org
-- context; the app connects as convoy_website_app, which cannot bypass RLS.
-- The website DB never mirrors domain state: runs, plans, steps, and
-- checkpoints are read through the control plane.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'convoy_website_app') THEN
    CREATE ROLE convoy_website_app LOGIN PASSWORD 'convoy_website_app';
  END IF;
END
$$;

-- Per-transaction context, set by the app on every connection checkout.
-- Empty-string GUCs read as NULL so unset context denies instead of erroring.
CREATE OR REPLACE FUNCTION app_org_id() RETURNS uuid AS $$
  SELECT NULLIF(current_setting('app.org_id', true), '')::uuid
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION app_user_id() RETURNS uuid AS $$
  SELECT NULLIF(current_setting('app.user_id', true), '')::uuid
$$ LANGUAGE sql STABLE;

CREATE TABLE organizations (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workos_org_id text UNIQUE,
  tenant_id     text NOT NULL UNIQUE,
  name          text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workos_user_id text UNIQUE,
  email          text NOT NULL UNIQUE,
  name           text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE memberships (
  org_id       uuid NOT NULL REFERENCES organizations(id),
  user_id      uuid NOT NULL REFERENCES users(id),
  role         text NOT NULL CHECK (role IN ('admin', 'operator', 'member', 'viewer')),
  capabilities text[] NOT NULL DEFAULT '{}',
  status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'pending', 'suspended')),
  invited_by   uuid REFERENCES users(id),
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, user_id)
);

CREATE TABLE invites (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     uuid NOT NULL REFERENCES organizations(id),
  email      text NOT NULL,
  role       text NOT NULL CHECK (role IN ('admin', 'operator', 'member', 'viewer')),
  token      text NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  status     text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'revoked', 'expired')),
  invited_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE teams (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     uuid NOT NULL REFERENCES organizations(id),
  name       text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, name)
);

CREATE TABLE team_memberships (
  team_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES users(id),
  PRIMARY KEY (team_id, user_id)
);

CREATE TABLE routine_assignments (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        uuid NOT NULL REFERENCES organizations(id),
  routine_id    text NOT NULL,
  assignee_type text NOT NULL CHECK (assignee_type IN ('user', 'team')),
  assignee_id   uuid NOT NULL,
  relationship  text NOT NULL CHECK (relationship IN ('owner', 'approver', 'watcher')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, routine_id, assignee_type, assignee_id, relationship)
);

-- Written only by the notifier, idempotent on the runtime's gapless event
-- id (run_id:seq). Bell panel and Checkpoints read these same rows.
CREATE TABLE notifications (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     uuid NOT NULL REFERENCES organizations(id),
  user_id    uuid NOT NULL REFERENCES users(id),
  event_id   text NOT NULL,
  run_id     text NOT NULL,
  class      text NOT NULL,
  title      text NOT NULL,
  cta_url    text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  read_at    timestamptz,
  UNIQUE (event_id, user_id)
);

-- The notifier's org events-feed checkpoint; the cursor only moves forward.
CREATE TABLE notifier_state (
  org_id     uuid PRIMARY KEY REFERENCES organizations(id),
  cursor_seq bigint NOT NULL DEFAULT 0
);

-- Open human checkpoints with deadlines, populated from gate_opened events
-- and swept for deadline-approaching notices. org_id realizes the RLS
-- discipline over the design sketch's (run_id, step_id, deadline) shape.
CREATE TABLE open_gates (
  org_id      uuid NOT NULL REFERENCES organizations(id),
  run_id      text NOT NULL,
  step_id     text NOT NULL,
  deadline    timestamptz,
  notified_at timestamptz,
  PRIMARY KEY (run_id, step_id)
);

CREATE TABLE notification_prefs (
  user_id  uuid NOT NULL REFERENCES users(id),
  org_id   uuid NOT NULL REFERENCES organizations(id),
  class    text NOT NULL,
  channels text[] NOT NULL DEFAULT '{in_app}',
  PRIMARY KEY (user_id, org_id, class)
);

-- Website-side capture of the shared core/ Feedback shape, consumed by
-- learning/ (D12). learning_status tracks the handoff lifecycle.
CREATE TABLE feedback (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          uuid NOT NULL REFERENCES organizations(id),
  run_id          text NOT NULL,
  step_id         text,
  author_id       uuid NOT NULL REFERENCES users(id),
  kind            text NOT NULL CHECK (kind IN ('rating', 'comment', 'correction')),
  rating          int CHECK (rating BETWEEN 1 AND 5),
  body            text,
  learning_status text NOT NULL DEFAULT 'new' CHECK (learning_status IN ('new', 'queued', 'consumed')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT rating_requires_value CHECK (kind <> 'rating' OR rating IS NOT NULL)
);

CREATE TABLE catalog_entries (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  publisher_org_id        uuid NOT NULL REFERENCES organizations(id),
  routine_version_ref     text NOT NULL,
  capability_requirements jsonb NOT NULL DEFAULT '{}',
  scenario_suite_ref      text,
  eval_thresholds         jsonb NOT NULL DEFAULT '{}',
  visibility              text NOT NULL DEFAULT 'convoy' CHECK (visibility IN ('convoy', 'private', 'public')),
  storefront              jsonb NOT NULL DEFAULT '{}',
  changelog               jsonb NOT NULL DEFAULT '[]',
  created_at              timestamptz NOT NULL DEFAULT now()
);

-- Invoice-first Stripe (D11): plan and status arrive by webhook; invoices
-- and payment methods live behind Stripe-hosted surfaces.
CREATE TABLE billing_accounts (
  org_id             uuid PRIMARY KEY REFERENCES organizations(id),
  stripe_customer_id text UNIQUE,
  plan               text,
  status             text,
  updated_at         timestamptz NOT NULL DEFAULT now()
);

-- Website-side administrative actions only; domain actions are audited by
-- the runtime's event log.
CREATE TABLE admin_audit (
  id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id   uuid NOT NULL REFERENCES organizations(id),
  actor_id uuid NOT NULL REFERENCES users(id),
  action   text NOT NULL,
  subject  text NOT NULL,
  ts       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_memberships_user ON memberships (user_id);
CREATE INDEX idx_invites_org ON invites (org_id, status);
CREATE INDEX idx_teams_org ON teams (org_id);
CREATE INDEX idx_assignments_routine ON routine_assignments (org_id, routine_id);
CREATE INDEX idx_notifications_inbox ON notifications (org_id, user_id, read_at, created_at DESC);
CREATE INDEX idx_open_gates_deadline ON open_gates (deadline) WHERE deadline IS NOT NULL;
CREATE INDEX idx_feedback_run ON feedback (org_id, run_id);
CREATE INDEX idx_feedback_learning ON feedback (learning_status) WHERE learning_status <> 'consumed';
CREATE INDEX idx_admin_audit_org ON admin_audit (org_id, ts DESC);

-- Row-level security. The app role owns no bypass; a connection without org
-- context sees nothing.
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE invites ENABLE ROW LEVEL SECURITY;
ALTER TABLE teams ENABLE ROW LEVEL SECURITY;
ALTER TABLE team_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE routine_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifier_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE open_gates ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification_prefs ENABLE ROW LEVEL SECURITY;
ALTER TABLE feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalog_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_audit ENABLE ROW LEVEL SECURITY;

-- Organizations: the active org, plus any org the user belongs to (the org
-- switcher lists them). Creation requires an authenticated user context.
CREATE POLICY org_select ON organizations FOR SELECT USING (
  id = app_org_id()
  OR EXISTS (
    SELECT 1 FROM memberships m
    WHERE m.org_id = organizations.id AND m.user_id = app_user_id()
  )
);
CREATE POLICY org_insert ON organizations FOR INSERT WITH CHECK (app_user_id() IS NOT NULL);
CREATE POLICY org_update ON organizations FOR UPDATE USING (id = app_org_id());

-- Users: yourself, plus people who share the active org (member pickers,
-- assignee chips). Identity rows are written during sign-in sync.
CREATE POLICY users_select ON users FOR SELECT USING (
  id = app_user_id()
  OR EXISTS (
    SELECT 1 FROM memberships m
    WHERE m.user_id = users.id AND m.org_id = app_org_id()
  )
);
CREATE POLICY users_insert ON users FOR INSERT WITH CHECK (true);
CREATE POLICY users_update ON users FOR UPDATE USING (id = app_user_id());

-- Memberships: rows of the active org, plus your own rows in any org so the
-- org switcher can resolve them before a switch.
CREATE POLICY memberships_select ON memberships FOR SELECT USING (
  org_id = app_org_id() OR user_id = app_user_id()
);
CREATE POLICY memberships_write ON memberships FOR INSERT WITH CHECK (
  org_id = app_org_id() OR app_org_id() IS NULL AND user_id = app_user_id()
);
CREATE POLICY memberships_update ON memberships FOR UPDATE USING (org_id = app_org_id());
CREATE POLICY memberships_delete ON memberships FOR DELETE USING (org_id = app_org_id());

CREATE POLICY org_scoped ON invites FOR ALL USING (org_id = app_org_id())
  WITH CHECK (org_id = app_org_id());
CREATE POLICY org_scoped ON teams FOR ALL USING (org_id = app_org_id())
  WITH CHECK (org_id = app_org_id());
CREATE POLICY org_scoped ON team_memberships FOR ALL USING (
  EXISTS (SELECT 1 FROM teams t WHERE t.id = team_memberships.team_id AND t.org_id = app_org_id())
) WITH CHECK (
  EXISTS (SELECT 1 FROM teams t WHERE t.id = team_memberships.team_id AND t.org_id = app_org_id())
);
CREATE POLICY org_scoped ON routine_assignments FOR ALL USING (org_id = app_org_id())
  WITH CHECK (org_id = app_org_id());
CREATE POLICY org_scoped ON notifier_state FOR ALL USING (org_id = app_org_id())
  WITH CHECK (org_id = app_org_id());
CREATE POLICY org_scoped ON open_gates FOR ALL USING (org_id = app_org_id())
  WITH CHECK (org_id = app_org_id());
CREATE POLICY org_scoped ON feedback FOR ALL USING (org_id = app_org_id())
  WITH CHECK (org_id = app_org_id());
CREATE POLICY org_scoped ON billing_accounts FOR ALL USING (org_id = app_org_id())
  WITH CHECK (org_id = app_org_id());
CREATE POLICY org_scoped ON admin_audit FOR ALL USING (org_id = app_org_id())
  WITH CHECK (org_id = app_org_id());

-- Notifications: your own inbox within the active org. The notifier writes
-- rows for any member of the org it is processing.
CREATE POLICY notifications_select ON notifications FOR SELECT USING (
  org_id = app_org_id() AND user_id = app_user_id()
);
CREATE POLICY notifications_insert ON notifications FOR INSERT WITH CHECK (org_id = app_org_id());
CREATE POLICY notifications_update ON notifications FOR UPDATE USING (
  org_id = app_org_id() AND user_id = app_user_id()
);

CREATE POLICY prefs_own ON notification_prefs FOR ALL USING (
  org_id = app_org_id() AND user_id = app_user_id()
) WITH CHECK (org_id = app_org_id() AND user_id = app_user_id());

-- Catalog: entries published by the active org, plus Convoy-published
-- entries visible to every org's storefront.
CREATE POLICY catalog_select ON catalog_entries FOR SELECT USING (
  publisher_org_id = app_org_id() OR visibility = 'convoy'
);
CREATE POLICY catalog_write ON catalog_entries FOR INSERT WITH CHECK (publisher_org_id = app_org_id());
CREATE POLICY catalog_update ON catalog_entries FOR UPDATE USING (publisher_org_id = app_org_id());

GRANT USAGE ON SCHEMA public TO convoy_website_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO convoy_website_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO convoy_website_app;
