-- Bring your own model keys: an organization's corporate Anthropic or
-- OpenAI key, sealed at rest and issued to a run only when the runtime asks
-- for it. The governing rule for everything downstream of this table is that
-- key material can be set, rotated, verified, and removed, but never read
-- back to a person: no query here, no policy, and no function returns the
-- plaintext, and the one column that carries the secret is ciphertext the
-- website cannot open without the key-encryption key held outside the
-- database.
--
-- The website still mirrors no domain state: runs, plans, and checkpoints
-- stay behind the control plane. What lands here is one org-singular fact
-- per provider (which corporate key an organization has handed us), so the
-- table is keyed by (org_id, provider) and holds at most two rows per org.

CREATE TABLE org_model_credentials (
  org_id        uuid NOT NULL REFERENCES organizations(id),
  -- The two providers we can seal a key for today. A CHECK rather than a
  -- lookup table: the set is fixed by the code that knows how to verify each
  -- one, and widening it is a code change, not a data change.
  provider      text NOT NULL CHECK (provider IN ('anthropic', 'openai')),
  -- The sealed key: AES-256-GCM output laid out as iv (12 bytes) ||
  -- authTag (16 bytes) || data, with the additional-authenticated-data
  -- binding it to this exact row and key version. Nothing but the
  -- application, holding the key-encryption key, can open it; the database
  -- stores opaque bytes.
  ciphertext    bytea NOT NULL,
  -- Which key-encryption key sealed this row. Rotation re-seals every row to
  -- a new version rather than migrating the schema, so issuance can refuse a
  -- row it does not have the key for instead of guessing.
  kek_version   smallint NOT NULL,
  -- A sha256 of the plaintext, so an admin can tell two keys apart and
  -- confirm a rotation actually changed the key without the key being shown.
  -- A hash is not the key and cannot be turned back into it.
  key_fingerprint text NOT NULL,
  -- The last four characters of the key, the one fragment we deliberately
  -- surface so a person recognizes which key is configured. Four characters
  -- identify without revealing, the same bargain a card's last four strikes.
  key_last4     text NOT NULL,
  set_by        uuid NOT NULL REFERENCES users(id),
  set_at        timestamptz NOT NULL DEFAULT now(),
  -- When the key last authenticated against its provider, and the recorded
  -- outcome of that check. Both null until the first verification lands.
  verified_at   timestamptz,
  verify_status text,
  PRIMARY KEY (org_id, provider)
);

ALTER TABLE org_model_credentials ENABLE ROW LEVEL SECURITY;

-- This table is stricter than the org_scoped tables in 0001 and 0006. There,
-- any member of the active org may read a row; here, even reading the
-- ciphertext requires an active admin membership, because the ciphertext is
-- the sealed secret and only an organization admin has any business handling
-- it. The predicate is identical in USING and WITH CHECK, so the same admin
-- gate governs SELECT, INSERT, UPDATE, and DELETE alike: a non-admin member
-- sees no rows at all, and the RLS check is a second lock behind the
-- application's own admin gate rather than a restatement of it.
CREATE POLICY credential_admin_only ON org_model_credentials FOR ALL
  USING (
    org_id = app_org_id()
    AND EXISTS (
      SELECT 1 FROM memberships m
       WHERE m.org_id = org_model_credentials.org_id
         AND m.user_id = app_user_id()
         AND m.role = 'admin'
         AND m.status = 'active'
    )
  )
  WITH CHECK (
    org_id = app_org_id()
    AND EXISTS (
      SELECT 1 FROM memberships m
       WHERE m.org_id = org_model_credentials.org_id
         AND m.user_id = app_user_id()
         AND m.role = 'admin'
         AND m.status = 'active'
    )
  );

-- The runtime issuance read has no person in context: when a run needs its
-- organization's key, the app process asks for it under its own service
-- identity, with neither an org nor a user set, so the admin-only policy
-- above would (correctly) return nothing. This SECURITY DEFINER function is
-- the one sanctioned way across that gap, and it is safe for three reasons
-- taken together: the only caller is the app process itself, running under
-- its service identity on the internal network, never a browser; the
-- function exposes exactly the two columns needed to unseal the row and
-- nothing that widens the surface (no fingerprint, no set_by, no plaintext,
-- which the database could not produce anyway); and the app gates issuance
-- in code by the run's ownership before it ever calls here, so the function
-- trusts its caller to have established that a given run belongs to a given
-- org. It follows the 0003 precedent exactly: definer rights, a pinned
-- search_path, execute revoked from PUBLIC and granted only to the app role.
CREATE OR REPLACE FUNCTION model_credential_for_issuance(p_org_id uuid, p_provider text)
RETURNS TABLE (ciphertext bytea, kek_version smallint)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT c.ciphertext, c.kek_version
    FROM org_model_credentials c
   WHERE c.org_id = p_org_id
     AND c.provider = p_provider
$$;

REVOKE ALL ON FUNCTION model_credential_for_issuance(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION model_credential_for_issuance(uuid, text) TO convoy_website_app;
