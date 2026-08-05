-- A settings home for organizations.
--
-- The Admin area carries org policies (budget defaults) and org
-- notification defaults, but the organizations table carries no column
-- to hold them. These are org-singular facts (one policy set, one
-- defaults map per org), so they belong inside the organizations table
-- itself rather than in a new table: a jsonb settings column realizes
-- them without widening the schema.
--
-- Shape (validated by zod in src/lib/orgs/settings.ts, the only writer):
--   settings.policies              -> budget defaults + viewer export toggle
--   settings.notification_defaults -> per-class default channels the
--                                     notifier falls back to when a user
--                                     has no notification_prefs row
--
-- RLS is unchanged: organizations' existing policies already scope reads
-- to members and writes to the active org context.

ALTER TABLE organizations ADD COLUMN settings jsonb NOT NULL DEFAULT '{}';
