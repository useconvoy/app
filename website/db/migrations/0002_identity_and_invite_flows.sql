-- Two narrow flows that 0001's org/user-scoped policies cannot express.
-- Both are additive (permissive policies OR-combine); nothing in 0001 is
-- rewritten or weakened.
--
-- Constraint 1: identity sync at sign-in runs before any user context can
-- exist. The app knows only an email until the users row is found or
-- created, so a by-email upsert cannot pass users_select/users_update
-- (both require app_user_id() = id). A SECURITY DEFINER function owned by
-- the migration role performs exactly that upsert and nothing else.
--
-- Constraint 2: invite acceptance. The invitee holds a token but no
-- membership yet, so the org-scoped policies on invites and organizations
-- hide the very row they need to read (and the invite row they must mark
-- accepted). A transaction-local app.invite_token GUC scopes visibility to
-- the single presented token.

CREATE OR REPLACE FUNCTION app_invite_token() RETURNS text AS $$
  SELECT NULLIF(current_setting('app.invite_token', true), '')
$$ LANGUAGE sql STABLE;

-- The presented token reveals its own invite row only.
CREATE POLICY invite_select_by_token ON invites FOR SELECT USING (
  token = app_invite_token()
);

-- Acceptance flips status on that same row; the token does not change, so
-- the check clause holds after the update.
CREATE POLICY invite_update_by_token ON invites FOR UPDATE USING (
  token = app_invite_token()
) WITH CHECK (token = app_invite_token());

-- The invite page shows which organization is inviting before membership
-- exists. Visibility rides the token-scoped invite policy above.
CREATE POLICY org_select_by_invite ON organizations FOR SELECT USING (
  EXISTS (
    SELECT 1 FROM invites i
    WHERE i.org_id = organizations.id AND i.token = app_invite_token()
  )
);

-- Sign-in identity sync: upsert by email, keeping an existing WorkOS id
-- when the new sign-in does not carry one. Runs as the table owner because
-- no user context exists yet; callable only by the app role.
CREATE OR REPLACE FUNCTION sync_user_identity(p_email text, p_name text, p_workos_user_id text)
RETURNS TABLE (id uuid, email text, name text)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  INSERT INTO users AS u (email, name, workos_user_id)
  VALUES (lower(p_email), p_name, p_workos_user_id)
  ON CONFLICT (email) DO UPDATE SET
    name = EXCLUDED.name,
    workos_user_id = COALESCE(EXCLUDED.workos_user_id, u.workos_user_id)
  RETURNING u.id, u.email, u.name
$$;

REVOKE ALL ON FUNCTION sync_user_identity(text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sync_user_identity(text, text, text) TO convoy_website_app;
