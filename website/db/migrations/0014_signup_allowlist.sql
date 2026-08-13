-- Early access: sign-in is gated on an allowlist while the platform runs
-- a waitlist. An entry is a lowercased full email, or "@domain.com" to
-- admit a whole domain. People who already have an account keep signing
-- in regardless (the gate is on NEW identities, not existing ones).
--
-- The check runs before any session exists, so two things here work
-- outside org/user context: the table's SELECT policy is open (entries
-- are addresses, not secrets, and the app role still cannot write
-- without an authenticated context), and existing-account detection goes
-- through a SECURITY DEFINER helper because the users table's RLS hides
-- everyone from an anonymous connection.

CREATE TABLE signup_allowlist (
  email      text PRIMARY KEY CHECK (email = lower(email)),
  note       text,
  added_by   uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE signup_allowlist ENABLE ROW LEVEL SECURITY;

CREATE POLICY signup_allowlist_select ON signup_allowlist
  FOR SELECT USING (true);
CREATE POLICY signup_allowlist_insert ON signup_allowlist
  FOR INSERT WITH CHECK (app_user_id() IS NOT NULL);
CREATE POLICY signup_allowlist_delete ON signup_allowlist
  FOR DELETE USING (app_user_id() IS NOT NULL);

CREATE OR REPLACE FUNCTION signup_email_known(p_email text)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT EXISTS (SELECT 1 FROM users WHERE lower(email) = lower(p_email));
$$;

REVOKE ALL ON FUNCTION signup_email_known(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION signup_email_known(text) TO convoy_website_app;
