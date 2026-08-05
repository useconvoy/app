"""Activity names — the only activity surface workflow code imports.

Workflows schedule activities by name so the workflow sandbox never imports
activity implementations (which pull in I/O clients). Pure constants only.
"""

CREATE_PLAN = "create_plan"
ARCHIVE_PLAN_SNAPSHOT = "archive_plan_snapshot"
RUN_TURN = "run_turn"
LAND_RUN = "land_run"
EMIT_RUN_EVENTS = "emit_run_events"
ASSEMBLE_PINNED_HEADER = "assemble_pinned_header"
PROVISION_MODEL_KEY = "provision_model_key"
ASSEMBLE_SUBAGENT_HEADER = "assemble_subagent_header"
WRAP_SUBAGENT_RESULT = "wrap_subagent_result"
ARCHIVE_SUBAGENT_RESULT = "archive_subagent_result"
COMPACT_STEP = "compact_step"
RUN_PROMOTED_TOOL = "run_promoted_tool"
RUN_SANDBOX_JOB = "run_sandbox_job"
