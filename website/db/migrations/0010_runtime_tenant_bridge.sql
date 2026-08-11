-- Runtime tenant bridge. The environments registry owns and validates the
-- tenant id on every immutable binding. Once a website organization is
-- linked to that registry, control-plane calls therefore use the linked
-- registry organization id. Unlinked/table-backed organizations retain the
-- original website tenant id.
--
-- list_organizations() is the no-person discovery seam used by the notifier
-- and model-credential lookup. Keeping the same return signature lets both
-- consumers follow the runtime tenant without widening what the function
-- exposes or weakening RLS.

CREATE OR REPLACE FUNCTION list_organizations()
RETURNS TABLE (id uuid, tenant_id text)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT o.id, COALESCE(o.environments_org_id, o.tenant_id)
  FROM organizations o
$$;

REVOKE ALL ON FUNCTION list_organizations() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION list_organizations() TO convoy_website_app;
