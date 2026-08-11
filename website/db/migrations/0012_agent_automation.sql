-- Fold the former routine/job definition into the durable Agent record.
-- After this migration there is no product-side routines table: an Agent
-- owns both its runtime configuration and the work it performs.

BEGIN;

ALTER TABLE agents
  ADD COLUMN IF NOT EXISTS goal text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS systems text[] NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS budget_cap_usd numeric(12,2) NOT NULL DEFAULT 0
    CHECK (budget_cap_usd >= 0),
  ADD COLUMN IF NOT EXISTS plan_steps jsonb NOT NULL DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS schedule_description text,
  ADD COLUMN IF NOT EXISTS source_entry_id uuid REFERENCES catalog_entries(id),
  ADD COLUMN IF NOT EXISTS source_version int,
  ADD COLUMN IF NOT EXISTS automation_configured boolean NOT NULL DEFAULT false;

-- Preserve every existing job. The first job assigned to an Agent enriches
-- that Agent in place. If old data assigned several jobs to one runtime
-- profile, clone the runtime configuration so each job becomes one Agent.
-- Unassigned/orphaned jobs receive a runnable Agent rather than being lost.
CREATE TEMP TABLE routine_agent_migration (
  routine_id uuid PRIMARY KEY,
  agent_id uuid NOT NULL
) ON COMMIT DROP;

DO $$
DECLARE
  job record;
  target_agent_id uuid;
  target_workspace_id uuid;
  base_agent record;
  workspace_row record;
  first_job_for_agent boolean;
BEGIN
  -- A deploy may be recovering from a manually applied or interrupted copy
  -- of this migration. The column additions above are already idempotent;
  -- if the source table is gone there is no legacy data left to fold.
  IF to_regclass('public.routines') IS NULL THEN
    RETURN;
  END IF;

  FOR job IN
    SELECT r.*
      FROM routines r
     ORDER BY r.created_at, r.id
  LOOP
    first_job_for_agent := job.agent_id IS NOT NULL
      AND NOT EXISTS (
        SELECT 1 FROM routine_agent_migration m WHERE m.agent_id = job.agent_id
      );

    IF first_job_for_agent THEN
      target_agent_id := job.agent_id;
    ELSE
      target_workspace_id := job.workspace_id;

      -- A deleted workspace used to leave a job detached. Recover it into a
      -- clearly named workspace so the Agent remains visible and editable.
      IF target_workspace_id IS NULL THEN
        SELECT w.id INTO target_workspace_id
          FROM workspaces w
         WHERE w.org_id = job.org_id
           AND w.name = 'Recovered agents'
         ORDER BY w.created_at
         LIMIT 1;

        IF target_workspace_id IS NULL THEN
          INSERT INTO workspaces
            (org_id, name, purpose, environment_id,
             rehearsal_environment_id, systems, clock_mode, versions)
          VALUES
            (job.org_id, 'Recovered agents',
             'Agents recovered from jobs whose workspace was removed.',
             'prod-local', 'stub-local', '[]', 'wall',
             jsonb_build_array(jsonb_build_object(
               'version', 1,
               'note', 'Created during Agent migration',
               'createdAt', now()::text
             )))
          RETURNING id INTO target_workspace_id;
        END IF;
      END IF;

      SELECT a.* INTO base_agent
        FROM agents a
       WHERE a.org_id = job.org_id
         AND a.workspace_id = target_workspace_id
       ORDER BY
         (a.id = job.agent_id) DESC,
         a.is_default DESC,
         a.created_at,
         a.id
       LIMIT 1;

      SELECT w.* INTO workspace_row
        FROM workspaces w
       WHERE w.id = target_workspace_id;

      target_agent_id := gen_random_uuid();
      INSERT INTO agents (
        id, org_id, workspace_id, registry_environment_id, name, purpose,
        production_binding_id, rehearsal_binding_id, sandbox_template,
        browser_policy, version, is_default, created_by, created_at, updated_at
      ) VALUES (
        target_agent_id,
        job.org_id,
        target_workspace_id,
        NULL,
        COALESCE(NULLIF(job.name, ''), 'Recovered agent') || ' ' || left(job.id::text, 8),
        COALESCE(NULLIF(job.descriptor, ''), job.name, ''),
        COALESCE(base_agent.production_binding_id, workspace_row.environment_id),
        COALESCE(base_agent.rehearsal_binding_id, workspace_row.rehearsal_environment_id),
        COALESCE(base_agent.sandbox_template, 'convoy-devbox-python'),
        base_agent.browser_policy,
        COALESCE(base_agent.version, 1),
        false,
        COALESCE(job.created_by, base_agent.created_by),
        job.created_at,
        job.updated_at
      );
    END IF;

    INSERT INTO routine_agent_migration (routine_id, agent_id)
    VALUES (job.id, target_agent_id);

    UPDATE agents
       SET goal = CASE
             WHEN job.descriptor <> '' THEN job.name || ': ' || job.descriptor
             ELSE job.name
           END,
           systems = job.systems,
           budget_cap_usd = job.budget_cap_usd,
           plan_steps = job.plan_steps,
           schedule_description = job.schedule_description,
           source_entry_id = job.source_entry_id,
           source_version = job.source_version,
           automation_configured = true,
           updated_at = GREATEST(agents.updated_at, job.updated_at)
     WHERE id = target_agent_id;
  END LOOP;
END $$;

-- Move evaluation history and assignments to Agent ids before removing the
-- old table. Assignment ids intentionally remain text because runtime-origin
-- records may use stable non-UUID Agent identifiers. This block is guarded so
-- the normal migrator can recover when the schema was applied outside its
-- ledger and safely record the version on a retry.
DO $$
BEGIN
  IF to_regclass('public.routines') IS NOT NULL THEN
    ALTER TABLE routine_eval_scores
      DROP CONSTRAINT IF EXISTS routine_eval_scores_routine_id_fkey;

    UPDATE routine_eval_scores score
       SET routine_id = mapping.agent_id
      FROM routine_agent_migration mapping
     WHERE score.routine_id = mapping.routine_id;

    ALTER TABLE routine_eval_scores RENAME TO agent_eval_scores;
    ALTER TABLE agent_eval_scores RENAME COLUMN routine_id TO agent_id;
    ALTER TABLE agent_eval_scores
      ADD CONSTRAINT agent_eval_scores_agent_id_fkey
      FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE;
    ALTER INDEX idx_eval_scores_routine RENAME TO idx_eval_scores_agent;

    UPDATE routine_assignments assignment
       SET routine_id = mapping.agent_id::text
      FROM routine_agent_migration mapping
     WHERE assignment.routine_id = mapping.routine_id::text;

    ALTER TABLE routine_assignments RENAME TO agent_assignments;
    ALTER TABLE agent_assignments RENAME COLUMN routine_id TO agent_id;
    ALTER INDEX idx_assignments_routine RENAME TO idx_assignments_agent;

    DROP INDEX IF EXISTS idx_routines_agent;
    DROP TABLE routines;
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_agents_source_entry
  ON agents (org_id, source_entry_id) WHERE source_entry_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_agents_configured
  ON agents (org_id, automation_configured, created_at);

COMMIT;
