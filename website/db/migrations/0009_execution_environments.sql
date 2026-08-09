-- Named runtime environments are distinct from workspaces. A workspace owns
-- systems and routines; each environment beneath it selects the concrete
-- runtime binding plus compute/browser/checkpoint settings used by a run.

CREATE TABLE execution_environments (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   uuid NOT NULL REFERENCES organizations(id),
  workspace_id             uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  registry_environment_id  text,
  name                     text NOT NULL,
  purpose                  text NOT NULL DEFAULT '',
  production_binding_id    text NOT NULL,
  rehearsal_binding_id     text NOT NULL,
  sandbox_template         text NOT NULL DEFAULT 'convoy-devbox-python',
  browser_policy           jsonb,
  version                  int NOT NULL DEFAULT 1 CHECK (version > 0),
  is_default               boolean NOT NULL DEFAULT false,
  created_by               uuid REFERENCES users(id),
  created_at               timestamptz NOT NULL DEFAULT now(),
  updated_at               timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_execution_environments_workspace
  ON execution_environments (org_id, workspace_id, created_at);
CREATE UNIQUE INDEX uq_execution_environments_name
  ON execution_environments (org_id, workspace_id, lower(name));
CREATE UNIQUE INDEX uq_execution_environments_registry
  ON execution_environments (org_id, registry_environment_id)
  WHERE registry_environment_id IS NOT NULL;
CREATE UNIQUE INDEX uq_execution_environments_default
  ON execution_environments (org_id, workspace_id)
  WHERE is_default;

ALTER TABLE execution_environments ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_scoped ON execution_environments FOR ALL
  USING (org_id = app_org_id())
  WITH CHECK (org_id = app_org_id());
