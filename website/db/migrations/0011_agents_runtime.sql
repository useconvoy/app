-- The former execution_environments rows describe an agent's runtime
-- configuration, not a user-managed deployment environment. Preserve every
-- existing row and its registry bindings while making Agent the product
-- model that routines select.

ALTER TABLE execution_environments RENAME TO agents;

ALTER INDEX idx_execution_environments_workspace RENAME TO idx_agents_workspace;
ALTER INDEX uq_execution_environments_name RENAME TO uq_agents_name;
ALTER INDEX uq_execution_environments_registry RENAME TO uq_agents_registry;
ALTER INDEX uq_execution_environments_default RENAME TO uq_agents_default;

ALTER TABLE routines
  ADD COLUMN agent_id uuid REFERENCES agents(id) ON DELETE SET NULL;

-- Existing routines inherit the default agent in their workspace. If no
-- default was marked, use the oldest agent so old installs remain runnable.
UPDATE routines AS routine
   SET agent_id = (
    SELECT agent.id
      FROM agents AS agent
     WHERE agent.org_id = routine.org_id
       AND agent.workspace_id = routine.workspace_id
     ORDER BY agent.is_default DESC, agent.created_at, agent.id
     LIMIT 1
   )
 WHERE routine.agent_id IS NULL;

CREATE INDEX idx_routines_agent ON routines (agent_id) WHERE agent_id IS NOT NULL;
