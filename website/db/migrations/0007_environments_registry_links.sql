-- E0: the environments registry becomes real behind a flag
-- (CONVOY_ENVIRONMENTS_URL + CONVOY_ENVIRONMENTS_INTERNAL_TOKEN). The
-- website keeps its own uuid keys everywhere -- routes, routines
-- references, RLS -- and links each row to its registry counterpart
-- through these columns. Both stay null while the flag is off, so
-- everything already deployed keeps its table-backed behavior untouched.

-- The registry organization created for this org at provisioning time
-- (POST /organizations). Null means the org predates the flag or its
-- provisioning call failed; the adapter then falls back to the local
-- workspaces table for that org.
ALTER TABLE organizations ADD COLUMN environments_org_id text UNIQUE;

-- The registry workspace behind a local workspaces row. The local row
-- becomes a cache: system grants and the version notes render from here,
-- while the environment bindings and clock mode read straight from the
-- registry rows.
ALTER TABLE workspaces ADD COLUMN environments_workspace_id text;

CREATE UNIQUE INDEX uq_workspaces_environments_workspace
  ON workspaces (org_id, environments_workspace_id)
  WHERE environments_workspace_id IS NOT NULL;
