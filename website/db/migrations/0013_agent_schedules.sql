-- Agent schedules: the structured trigger definition. The runtime owns the
-- firing machinery (one Temporal Schedule per agent); this column is the
-- console's editable source of truth for the form and the "Starts:" copy.
-- Shape: {"cron": "0 9 * * 1-5", "timezone": "America/Chicago",
--         "target": "rehearsal" | "production", "enabled": true}
-- Null means the agent starts on demand only. The prose
-- schedule_description column stays as display copy for agents predating
-- structured schedules.

ALTER TABLE agents ADD COLUMN IF NOT EXISTS schedule jsonb;
