-- Constraint 3: finding the invitation you were already sent.
--
-- Migration 0002 scopes invite visibility to whoever presents the token, on
-- the reasoning that a person following their link is the only person who
-- needs to see it. That holds for the link, and it leaves a hole either side
-- of it: nothing emails the link today, so it reaches people by hand, and a
-- person who signs in without it has no way to discover the invitation
-- waiting for them. Onboarding could only offer "create an organization", so
-- someone joining a colleague's team made a second organization instead.
--
-- Let a signed-in user see invitations addressed to their own email. The
-- address is read out of the users table under app_user_id() rather than
-- taken from the request, so there is no input to vary and nothing to
-- enumerate: the policy reveals a row to exactly the person it was already
-- addressed to. Status and expiry are deliberately not filtered here.
-- Visibility is the policy's job; which invites are still worth offering is
-- the query's.

CREATE POLICY invite_select_own_email ON invites FOR SELECT USING (
  email = (SELECT u.email FROM users u WHERE u.id = app_user_id())
);

-- Same reason as org_select_by_invite in 0002: the chooser has to name the
-- organization doing the inviting, and that is a row the invitee cannot see
-- yet because the membership is what they are being offered.
CREATE POLICY org_select_by_pending_invite ON organizations FOR SELECT USING (
  EXISTS (
    SELECT 1
      FROM invites i
     WHERE i.org_id = organizations.id
       AND i.email = (SELECT u.email FROM users u WHERE u.id = app_user_id())
  )
);
