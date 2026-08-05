-- W3 notifier support. Two additive pieces; nothing in 0001/0002 is
-- rewritten or weakened.
--
-- Constraint 1: the notifier worker iterates every organization to run its
-- consumer and sweep ticks, but the app role's view of organizations is
-- membership/context bound and the worker holds neither a user nor an org
-- before it starts. A SECURITY DEFINER function exposes exactly the two
-- columns the worker needs to establish per-org context (id + tenant id)
-- and nothing else; each org's actual processing then runs under that
-- org's own RLS context.
--
-- Constraint 2: routing respects notification_prefs, but prefs_own (0001)
-- limits reads to the row owner. The notifier reads under org context with
-- no user id, so an org-scoped SELECT policy is added. Prefs are routing
-- switches (class -> channels), not sensitive data; writes stay owner-only.

CREATE OR REPLACE FUNCTION list_organizations()
RETURNS TABLE (id uuid, tenant_id text)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT o.id, o.tenant_id FROM organizations o
$$;

REVOKE ALL ON FUNCTION list_organizations() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION list_organizations() TO convoy_website_app;

CREATE POLICY prefs_org_read ON notification_prefs FOR SELECT USING (
  org_id = app_org_id()
);
