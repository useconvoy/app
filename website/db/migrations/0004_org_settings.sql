-- W6 admin close-out: a settings home for organizations.
--
-- DESIGN §5's Admin row promises "policies & budget defaults" and org
-- notification defaults, but §3's organizations table carries no column to
-- hold them. These are org-singular facts (one policy set, one defaults
-- map per org), so they belong inside the §3 organizations table itself
-- rather than in a new table the data model does not name: a jsonb
-- settings column realizes the Admin row without widening the schema
-- beyond DESIGN §3's table list.
--
-- Shape (validated by zod in src/lib/orgs/settings.ts, the only writer):
--   settings.policies              -> budget defaults + viewer export toggle (§9)
--   settings.notification_defaults -> per-class default channels the
--                                     notifier falls back to when a user
--                                     has no notification_prefs row
--
-- RLS is unchanged: organizations' existing policies already scope reads
-- to members and writes to the active org context.

ALTER TABLE organizations ADD COLUMN settings jsonb NOT NULL DEFAULT '{}';
