"""Initial PostgreSQL fleet schema, frozen from the qualified v1 data model.

This revision deliberately does not import live ORM metadata. Future model
changes require another explicit migration; a fresh install remains reproducible.
"""

from alembic import op

revision = "0001_fleet"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE audit_log (
        id BIGSERIAL NOT NULL,
        ts TIMESTAMP WITH TIME ZONE NOT NULL,
        actor_id VARCHAR(32),
        actor_email VARCHAR(255),
        action VARCHAR(64) NOT NULL,
        target VARCHAR(128),
        details JSON NOT NULL,
        PRIMARY KEY (id)
    )
    """)
    op.execute("CREATE INDEX ix_audit_log_action ON audit_log (action)")
    op.execute("CREATE INDEX ix_audit_log_ts ON audit_log (ts)")
    op.execute("""
    CREATE TABLE backups (
        id VARCHAR(32) NOT NULL,
        path TEXT NOT NULL,
        size BIGINT NOT NULL,
        sha256 VARCHAR(64) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        duration_ms FLOAT NOT NULL,
        PRIMARY KEY (id)
    )
    """)
    op.execute("""
    CREATE TABLE build_recipes (
        id VARCHAR(32) NOT NULL,
        name VARCHAR(255) NOT NULL,
        runtime_name VARCHAR(64) NOT NULL,
        source_repo VARCHAR(255) NOT NULL,
        commit VARCHAR(64) NOT NULL,
        tag VARCHAR(64),
        cmake_flags JSON NOT NULL,
        target JSON NOT NULL,
        backend VARCHAR(32) NOT NULL,
        digest VARCHAR(64) NOT NULL,
        created_by VARCHAR(32),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (digest)
    )
    """)
    op.execute("""
    CREATE TABLE devices (
        id VARCHAR(32) NOT NULL,
        name VARCHAR(255) NOT NULL,
        group_name VARCHAR(64) NOT NULL,
        profile_id VARCHAR(64) NOT NULL,
        simulated BOOLEAN NOT NULL,
        credential_hash VARCHAR(64),
        credential_issued_at TIMESTAMP WITH TIME ZONE,
        credential_revoked_at TIMESTAMP WITH TIME ZONE,
        credential_revoked_reason VARCHAR(64) NOT NULL,
        enrolled_at TIMESTAMP WITH TIME ZONE NOT NULL,
        rebound_at TIMESTAMP WITH TIME ZONE,
        last_seen_at TIMESTAMP WITH TIME ZONE,
        agent_version VARCHAR(32),
        boot_id VARCHAR(64),
        last_report_seq BIGINT NOT NULL,
        live_seq BIGINT NOT NULL,
        live_nonce VARCHAR(64),
        live_nonce_issued_at TIMESTAMP WITH TIME ZONE,
        binding_epoch BIGINT NOT NULL,
        live_at TIMESTAMP WITH TIME ZONE,
        generation BIGINT NOT NULL,
        active_operation_id VARCHAR(32),
        expected_active_release_id VARCHAR(32),
        intent_updated_at TIMESTAMP WITH TIME ZONE,
        intent_source JSON NOT NULL,
        observed_active_release_id VARCHAR(32),
        observed_recovery_release_id VARCHAR(32),
        observed_stage VARCHAR(32) NOT NULL,
        observed_health VARCHAR(16) NOT NULL,
        observed_generation BIGINT NOT NULL,
        observed_operation_id VARCHAR(32),
        observed_latch_generation BIGINT,
        observed_at TIMESTAMP WITH TIME ZONE,
        observed JSON NOT NULL,
        hardware JSON NOT NULL,
        runtime_evidence JSON NOT NULL,
        settings JSON NOT NULL,
        last_telemetry JSON NOT NULL,
        time_confidence VARCHAR(16) NOT NULL,
        notes TEXT NOT NULL,
        retired_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (id)
    )
    """)
    op.execute("CREATE INDEX ix_devices_active_operation_id ON devices (active_operation_id)")
    op.execute("CREATE INDEX ix_devices_credential_hash ON devices (credential_hash)")
    op.execute("CREATE INDEX ix_devices_expected_active_release_id ON devices (expected_active_release_id)")
    op.execute("CREATE INDEX ix_devices_group_name ON devices (group_name)")
    op.execute("CREATE INDEX ix_devices_last_seen_at ON devices (last_seen_at)")
    op.execute("CREATE INDEX ix_devices_name ON devices (name)")
    op.execute("CREATE INDEX ix_devices_observed_active_release_id ON devices (observed_active_release_id)")
    op.execute("CREATE INDEX ix_devices_simulated ON devices (simulated)")
    op.execute("""
    CREATE TABLE enrollment_tokens (
        id VARCHAR(32) NOT NULL,
        token_hash VARCHAR(64) NOT NULL,
        label VARCHAR(255) NOT NULL,
        group_name VARCHAR(64) NOT NULL,
        simulated BOOLEAN NOT NULL,
        rebind_device_id VARCHAR(32),
        created_by VARCHAR(32),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        consumed_at TIMESTAMP WITH TIME ZONE,
        consumed_request_id VARCHAR(64),
        consumed_secret_hash VARCHAR(64),
        device_id VARCHAR(32),
        revoked_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (id)
    )
    """)
    op.execute("CREATE UNIQUE INDEX ix_enrollment_tokens_token_hash ON enrollment_tokens (token_hash)")
    op.execute("""
    CREATE TABLE eval_results (
        id VARCHAR(32) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        device_id VARCHAR(32) NOT NULL,
        operation_id VARCHAR(32),
        release_id VARCHAR(32) NOT NULL,
        plan_id VARCHAR(32),
        eval_set_id VARCHAR(32) NOT NULL,
        device_verdict VARCHAR(16) NOT NULL,
        server_verdict VARCHAR(16) NOT NULL,
        gates JSON NOT NULL,
        summary JSON NOT NULL,
        cases JSON NOT NULL,
        coverage JSON NOT NULL,
        provenance JSON NOT NULL,
        simulated BOOLEAN NOT NULL,
        baseline BOOLEAN NOT NULL,
        stage VARCHAR(16) NOT NULL,
        PRIMARY KEY (id)
    )
    """)
    op.execute("CREATE INDEX ix_eval_results_created_at ON eval_results (created_at)")
    op.execute("CREATE INDEX ix_eval_results_device_id ON eval_results (device_id)")
    op.execute("CREATE INDEX ix_eval_results_eval_set_id ON eval_results (eval_set_id)")
    op.execute("CREATE INDEX ix_eval_results_operation_id ON eval_results (operation_id)")
    op.execute("CREATE INDEX ix_eval_results_plan_id ON eval_results (plan_id)")
    op.execute("CREATE INDEX ix_eval_results_release_id ON eval_results (release_id)")
    op.execute("""
    CREATE TABLE eval_sets (
        id VARCHAR(32) NOT NULL,
        name VARCHAR(255) NOT NULL,
        version VARCHAR(64) NOT NULL,
        digest VARCHAR(64) NOT NULL,
        cases JSON NOT NULL,
        scorer JSON NOT NULL,
        description TEXT NOT NULL,
        created_by VARCHAR(32),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_evalset_name_version UNIQUE (name, version)
    )
    """)
    op.execute("CREATE INDEX ix_eval_sets_digest ON eval_sets (digest)")
    op.execute("""
    CREATE TABLE failures (
        id VARCHAR(32) NOT NULL,
        ts TIMESTAMP WITH TIME ZONE NOT NULL,
        device_id VARCHAR(32),
        operation_id VARCHAR(32),
        release_id VARCHAR(32),
        trace_id VARCHAR(64),
        code VARCHAR(64) NOT NULL,
        stage VARCHAR(32),
        message TEXT NOT NULL,
        details JSON NOT NULL,
        simulated BOOLEAN NOT NULL,
        PRIMARY KEY (id)
    )
    """)
    op.execute("CREATE INDEX ix_failures_code ON failures (code)")
    op.execute("CREATE INDEX ix_failures_device_id ON failures (device_id)")
    op.execute("CREATE INDEX ix_failures_operation_id ON failures (operation_id)")
    op.execute("CREATE INDEX ix_failures_release_id ON failures (release_id)")
    op.execute("CREATE INDEX ix_failures_trace_id ON failures (trace_id)")
    op.execute("CREATE INDEX ix_failures_ts ON failures (ts)")
    op.execute("""
    CREATE TABLE fixture_models (
        repo VARCHAR(255) NOT NULL,
        revision VARCHAR(64) NOT NULL,
        files JSON NOT NULL,
        metadata JSON NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (repo, revision)
    )
    """)
    op.execute("""
    CREATE TABLE installation (
        id BIGSERIAL NOT NULL,
        schema_version BIGINT NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        quarantined_at TIMESTAMP WITH TIME ZONE,
        quarantine_reason TEXT NOT NULL,
        dispatch_paused_at TIMESTAMP WITH TIME ZONE,
        dispatch_pause_reason TEXT NOT NULL,
        restored_from JSON NOT NULL,
        PRIMARY KEY (id)
    )
    """)
    op.execute("""
    CREATE TABLE lane_cursors (
        device_id VARCHAR(32) NOT NULL,
        lane VARCHAR(16) NOT NULL,
        committed_seq BIGINT NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (device_id, lane)
    )
    """)
    op.execute("""
    CREATE TABLE log_entries (
        id BIGSERIAL NOT NULL,
        ts TIMESTAMP WITH TIME ZONE NOT NULL,
        device_id VARCHAR(32) NOT NULL,
        operation_id VARCHAR(32),
        trace_id VARCHAR(64),
        level VARCHAR(8) NOT NULL,
        source VARCHAR(32) NOT NULL,
        message TEXT NOT NULL,
        attrs JSON NOT NULL,
        PRIMARY KEY (id)
    )
    """)
    op.execute("CREATE INDEX ix_log_entries_device_id ON log_entries (device_id)")
    op.execute("CREATE INDEX ix_log_entries_operation_id ON log_entries (operation_id)")
    op.execute("CREATE INDEX ix_log_entries_trace_id ON log_entries (trace_id)")
    op.execute("CREATE INDEX ix_log_entries_ts ON log_entries (ts)")
    op.execute("CREATE INDEX ix_logs_device_ts ON log_entries (device_id, ts)")
    op.execute("""
    CREATE TABLE loss_ranges (
        id BIGSERIAL NOT NULL,
        device_id VARCHAR(32) NOT NULL,
        lane VARCHAR(16) NOT NULL,
        from_seq BIGINT NOT NULL,
        to_seq BIGINT NOT NULL,
        reason VARCHAR(64) NOT NULL,
        context JSON NOT NULL,
        reported_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id)
    )
    """)
    op.execute("CREATE INDEX ix_loss_ranges_device_id ON loss_ranges (device_id)")
    op.execute("""
    CREATE TABLE releases (
        id VARCHAR(32) NOT NULL,
        name VARCHAR(255) NOT NULL,
        version VARCHAR(64) NOT NULL,
        digest VARCHAR(64) NOT NULL,
        spec JSON NOT NULL,
        build_status VARCHAR(32) NOT NULL,
        runtime_artifact_id VARCHAR(32),
        recipe_id VARCHAR(32),
        eval_set_id VARCHAR(32),
        profile_id VARCHAR(64) NOT NULL,
        simulated BOOLEAN NOT NULL,
        weights_qualification VARCHAR(32) NOT NULL,
        notes TEXT NOT NULL,
        provenance JSON NOT NULL,
        created_by VARCHAR(32),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        retired_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (id),
        CONSTRAINT uq_release_name_version UNIQUE (name, version)
    )
    """)
    op.execute("CREATE INDEX ix_releases_created_at ON releases (created_at)")
    op.execute("CREATE UNIQUE INDEX ix_releases_digest ON releases (digest)")
    op.execute("CREATE INDEX ix_releases_name ON releases (name)")
    op.execute("CREATE INDEX ix_releases_simulated ON releases (simulated)")
    op.execute("""
    CREATE TABLE reports (
        id BIGSERIAL NOT NULL,
        device_id VARCHAR(32) NOT NULL,
        epoch BIGINT NOT NULL,
        seq BIGINT NOT NULL,
        boot_id VARCHAR(64),
        kind VARCHAR(16) NOT NULL,
        source_ts TIMESTAMP WITH TIME ZONE,
        receipt_ts TIMESTAMP WITH TIME ZONE NOT NULL,
        applied BOOLEAN NOT NULL,
        body JSON NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_report_seq UNIQUE (device_id, epoch, seq)
    )
    """)
    op.execute("CREATE INDEX ix_reports_device_id ON reports (device_id)")
    op.execute("CREATE INDEX ix_reports_receipt_ts ON reports (receipt_ts)")
    op.execute("""
    CREATE TABLE scheduler_leases (
        name VARCHAR(32) NOT NULL,
        owner VARCHAR(64) NOT NULL,
        token VARCHAR(64) NOT NULL,
        fence BIGINT NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        acquired_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (name)
    )
    """)
    op.execute("""
    CREATE TABLE schedules (
        id VARCHAR(32) NOT NULL,
        name VARCHAR(255) NOT NULL,
        kind VARCHAR(16) NOT NULL,
        cron VARCHAR(64) NOT NULL,
        timezone VARCHAR(64) NOT NULL,
        target JSON NOT NULL,
        payload JSON NOT NULL,
        missed_policy VARCHAR(16) NOT NULL,
        catchup_age_s BIGINT NOT NULL,
        window_s BIGINT NOT NULL,
        enabled BOOLEAN NOT NULL,
        paused_at TIMESTAMP WITH TIME ZONE,
        pause_reason VARCHAR(64) NOT NULL,
        owner_id VARCHAR(32),
        revision BIGINT NOT NULL,
        next_civil VARCHAR(32),
        next_run_at TIMESTAMP WITH TIME ZONE,
        last_run_at TIMESTAMP WITH TIME ZONE,
        last_result JSON NOT NULL,
        created_by VARCHAR(32),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id)
    )
    """)
    op.execute("CREATE INDEX ix_schedules_next_run_at ON schedules (next_run_at)")
    op.execute("""
    CREATE TABLE settings (
        key VARCHAR(64) NOT NULL,
        value JSON NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (key)
    )
    """)
    op.execute("""
    CREATE TABLE spans (
        id BIGSERIAL NOT NULL,
        trace_id VARCHAR(64) NOT NULL,
        span_id VARCHAR(32) NOT NULL,
        parent_span_id VARCHAR(32),
        device_id VARCHAR(32) NOT NULL,
        operation_id VARCHAR(32),
        name VARCHAR(128) NOT NULL,
        kind VARCHAR(32) NOT NULL,
        status VARCHAR(16) NOT NULL,
        start_ts TIMESTAMP WITH TIME ZONE NOT NULL,
        duration_ms FLOAT,
        attrs JSON NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_span UNIQUE (trace_id, span_id)
    )
    """)
    op.execute("CREATE INDEX ix_spans_device_id ON spans (device_id)")
    op.execute("CREATE INDEX ix_spans_operation_id ON spans (operation_id)")
    op.execute("CREATE INDEX ix_spans_start_ts ON spans (start_ts)")
    op.execute("CREATE INDEX ix_spans_trace_id ON spans (trace_id)")
    op.execute("""
    CREATE TABLE telemetry_samples (
        id BIGSERIAL NOT NULL,
        ts TIMESTAMP WITH TIME ZONE NOT NULL,
        device_id VARCHAR(32) NOT NULL,
        mem_total_mb FLOAT,
        mem_available_mb FLOAT,
        cpu_pct FLOAT,
        gpu_pct FLOAT,
        power_w FLOAT,
        temp_max_c FLOAT,
        disk_free_mb FLOAT,
        runtime_state VARCHAR(32),
        clock_confidence VARCHAR(16) NOT NULL,
        extra JSON NOT NULL,
        PRIMARY KEY (id)
    )
    """)
    op.execute("CREATE INDEX ix_telemetry_device_ts ON telemetry_samples (device_id, ts)")
    op.execute("CREATE INDEX ix_telemetry_samples_device_id ON telemetry_samples (device_id)")
    op.execute("CREATE INDEX ix_telemetry_samples_ts ON telemetry_samples (ts)")
    op.execute("""
    CREATE TABLE throttles (
        key VARCHAR(160) NOT NULL,
        window_start TIMESTAMP WITH TIME ZONE NOT NULL,
        count BIGINT NOT NULL,
        PRIMARY KEY (key)
    )
    """)
    op.execute("""
    CREATE TABLE usage_daily (
        id BIGSERIAL NOT NULL,
        day VARCHAR(10) NOT NULL,
        device_id VARCHAR(32) NOT NULL,
        metric VARCHAR(32) NOT NULL,
        value FLOAT NOT NULL,
        simulated BOOLEAN NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_usage UNIQUE (day, device_id, metric)
    )
    """)
    op.execute("CREATE INDEX ix_usage_daily_day ON usage_daily (day)")
    op.execute("CREATE INDEX ix_usage_daily_device_id ON usage_daily (device_id)")
    op.execute("""
    CREATE TABLE usage_unknown_intervals (
        id BIGSERIAL NOT NULL,
        device_id VARCHAR(32) NOT NULL,
        from_ts TIMESTAMP WITH TIME ZONE NOT NULL,
        to_ts TIMESTAMP WITH TIME ZONE NOT NULL,
        seconds FLOAT NOT NULL,
        reason VARCHAR(64) NOT NULL,
        previous_incarnation VARCHAR(64),
        record_seq BIGINT,
        simulated BOOLEAN NOT NULL,
        received_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id)
    )
    """)
    op.execute("CREATE INDEX ix_usage_unknown_intervals_device_id ON usage_unknown_intervals (device_id)")
    op.execute("""
    CREATE TABLE users (
        id VARCHAR(32) NOT NULL,
        email VARCHAR(255) NOT NULL,
        name VARCHAR(255) NOT NULL,
        role VARCHAR(16) NOT NULL,
        password_hash TEXT NOT NULL,
        disabled BOOLEAN NOT NULL,
        password_reset_required BOOLEAN NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id)
    )
    """)
    op.execute("CREATE UNIQUE INDEX ix_users_email ON users (email)")
    op.execute("""
    CREATE TABLE api_tokens (
        id VARCHAR(32) NOT NULL,
        user_id VARCHAR(32) NOT NULL,
        name VARCHAR(255) NOT NULL,
        prefix VARCHAR(16) NOT NULL,
        token_hash VARCHAR(64) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE,
        revoked_at TIMESTAMP WITH TIME ZONE,
        last_used_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (id),
        FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """)
    op.execute("CREATE UNIQUE INDEX ix_api_tokens_token_hash ON api_tokens (token_hash)")
    op.execute("CREATE INDEX ix_api_tokens_user_id ON api_tokens (user_id)")
    op.execute("""
    CREATE TABLE occurrences (
        id VARCHAR(32) NOT NULL,
        schedule_id VARCHAR(32) NOT NULL,
        civil_key VARCHAR(32) NOT NULL,
        scheduled_utc TIMESTAMP WITH TIME ZONE NOT NULL,
        revision BIGINT NOT NULL,
        frozen JSON NOT NULL,
        dispatched_at TIMESTAMP WITH TIME ZONE NOT NULL,
        status VARCHAR(24) NOT NULL,
        results JSON NOT NULL,
        finished_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (id),
        CONSTRAINT uq_occurrence_civil UNIQUE (schedule_id, civil_key),
        FOREIGN KEY(schedule_id) REFERENCES schedules (id) ON DELETE CASCADE
    )
    """)
    op.execute("CREATE INDEX ix_occurrences_schedule_id ON occurrences (schedule_id)")
    op.execute("""
    CREATE TABLE operations (
        id VARCHAR(32) NOT NULL,
        device_id VARCHAR(32) NOT NULL,
        type VARCHAR(16) NOT NULL,
        payload JSON NOT NULL,
        payload_digest VARCHAR(64) NOT NULL,
        generation BIGINT NOT NULL,
        expected_active_release_id VARCHAR(32),
        plan_id VARCHAR(32),
        release_id VARCHAR(32),
        status VARCHAR(24) NOT NULL,
        cancel_requested_at TIMESTAMP WITH TIME ZONE,
        deadline_at TIMESTAMP WITH TIME ZONE,
        delivered_at TIMESTAMP WITH TIME ZONE,
        delivery_count BIGINT NOT NULL,
        grant_id VARCHAR(32),
        outcome JSON NOT NULL,
        outcome_digest VARCHAR(64),
        progress JSON NOT NULL,
        trace_id VARCHAR(64),
        terminal_at TIMESTAMP WITH TIME ZONE,
        rollout_id VARCHAR(32),
        occurrence_id VARCHAR(32),
        created_by VARCHAR(64),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(device_id) REFERENCES devices (id)
    )
    """)
    op.execute("CREATE INDEX ix_operations_created_at ON operations (created_at)")
    op.execute("CREATE INDEX ix_operations_device_id ON operations (device_id)")
    op.execute("CREATE INDEX ix_operations_occurrence_id ON operations (occurrence_id)")
    op.execute("CREATE INDEX ix_operations_plan_id ON operations (plan_id)")
    op.execute("CREATE INDEX ix_operations_release_id ON operations (release_id)")
    op.execute("CREATE INDEX ix_operations_rollout_id ON operations (rollout_id)")
    op.execute("CREATE INDEX ix_operations_status ON operations (status)")
    op.execute("CREATE INDEX ix_operations_trace_id ON operations (trace_id)")
    op.execute("CREATE INDEX ix_operations_type ON operations (type)")
    op.execute("""
    CREATE TABLE plans (
        id VARCHAR(32) NOT NULL,
        name VARCHAR(255) NOT NULL,
        release_id VARCHAR(32) NOT NULL,
        baseline_release_id VARCHAR(32),
        eval_set_id VARCHAR(32) NOT NULL,
        gates JSON NOT NULL,
        workload JSON NOT NULL,
        sample_policy JSON NOT NULL,
        profile_id VARCHAR(64) NOT NULL,
        evaluator_version VARCHAR(16) NOT NULL,
        digest VARCHAR(64) NOT NULL,
        simulated BOOLEAN NOT NULL,
        created_by VARCHAR(32),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(release_id) REFERENCES releases (id),
        FOREIGN KEY(eval_set_id) REFERENCES eval_sets (id),
        UNIQUE (digest)
    )
    """)
    op.execute("CREATE INDEX ix_plans_release_id ON plans (release_id)")
    op.execute("""
    CREATE TABLE platform_mutation_receipts (
        id VARCHAR(32) NOT NULL,
        owner_user_id VARCHAR(32) NOT NULL,
        route VARCHAR(255) NOT NULL,
        key VARCHAR(128) NOT NULL,
        payload_digest VARCHAR(64) NOT NULL,
        response JSON NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (owner_user_id, route, key),
        FOREIGN KEY(owner_user_id) REFERENCES users (id)
    )
    """)
    op.execute(
        "CREATE INDEX ix_platform_mutation_receipts_owner_user_id ON platform_mutation_receipts (owner_user_id)"
    )
    op.execute("""
    CREATE TABLE platform_projects (
        id VARCHAR(32) NOT NULL,
        owner_user_id VARCHAR(32) NOT NULL,
        name VARCHAR(120) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(owner_user_id) REFERENCES users (id)
    )
    """)
    op.execute("CREATE INDEX ix_platform_projects_owner_user_id ON platform_projects (owner_user_id)")
    op.execute("""
    CREATE TABLE runtime_artifacts (
        id VARCHAR(32) NOT NULL,
        recipe_id VARCHAR(32) NOT NULL,
        archive_sha256 VARCHAR(64) NOT NULL,
        archive_size BIGINT NOT NULL,
        files JSON NOT NULL,
        provenance JSON NOT NULL,
        scope VARCHAR(64) NOT NULL,
        storage VARCHAR(16) NOT NULL,
        receipt JSON NOT NULL,
        verified VARCHAR(32) NOT NULL,
        created_by VARCHAR(64),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_artifact_scope UNIQUE (archive_sha256, scope),
        FOREIGN KEY(recipe_id) REFERENCES build_recipes (id)
    )
    """)
    op.execute("CREATE INDEX ix_runtime_artifacts_archive_sha256 ON runtime_artifacts (archive_sha256)")
    op.execute("CREATE INDEX ix_runtime_artifacts_recipe_id ON runtime_artifacts (recipe_id)")
    op.execute("""
    CREATE TABLE sessions (
        id VARCHAR(32) NOT NULL,
        user_id VARCHAR(32) NOT NULL,
        token_hash VARCHAR(64) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        revoked_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (id),
        FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """)
    op.execute("CREATE UNIQUE INDEX ix_sessions_token_hash ON sessions (token_hash)")
    op.execute("CREATE INDEX ix_sessions_user_id ON sessions (user_id)")
    op.execute("""
    CREATE TABLE grants (
        id VARCHAR(32) NOT NULL,
        operation_id VARCHAR(32) NOT NULL,
        device_id VARCHAR(32) NOT NULL,
        nonce VARCHAR(64) NOT NULL,
        boot_id VARCHAR(64),
        request_seq BIGINT NOT NULL,
        issued_at TIMESTAMP WITH TIME ZONE NOT NULL,
        ttl_s BIGINT NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        window_end TIMESTAMP WITH TIME ZONE,
        status VARCHAR(24) NOT NULL,
        consumed_seq BIGINT,
        PRIMARY KEY (id),
        CONSTRAINT uq_grant_nonce UNIQUE (operation_id, nonce),
        FOREIGN KEY(operation_id) REFERENCES operations (id)
    )
    """)
    op.execute("CREATE INDEX ix_grants_device_id ON grants (device_id)")
    op.execute("CREATE INDEX ix_grants_operation_id ON grants (operation_id)")
    op.execute("""
    CREATE TABLE platform_applications (
        id VARCHAR(32) NOT NULL,
        project_id VARCHAR(32) NOT NULL,
        name VARCHAR(120) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(project_id) REFERENCES platform_projects (id)
    )
    """)
    op.execute("CREATE INDEX ix_platform_applications_project_id ON platform_applications (project_id)")
    op.execute("""
    CREATE TABLE platform_robots (
        id VARCHAR(32) NOT NULL,
        project_id VARCHAR(32) NOT NULL,
        device_id VARCHAR(32) NOT NULL,
        name VARCHAR(120) NOT NULL,
        profile VARCHAR(80) NOT NULL,
        generation BIGINT NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(project_id) REFERENCES platform_projects (id),
        UNIQUE (device_id),
        FOREIGN KEY(device_id) REFERENCES devices (id)
    )
    """)
    op.execute("CREATE INDEX ix_platform_robots_project_id ON platform_robots (project_id)")
    op.execute("""
    CREATE TABLE rollouts (
        id VARCHAR(32) NOT NULL,
        name VARCHAR(255) NOT NULL,
        plan_id VARCHAR(32) NOT NULL,
        release_id VARCHAR(32) NOT NULL,
        targets JSON NOT NULL,
        canaries JSON NOT NULL,
        status VARCHAR(24) NOT NULL,
        device_states JSON NOT NULL,
        previous JSON NOT NULL,
        cursor BIGINT NOT NULL,
        message TEXT NOT NULL,
        simulated BOOLEAN NOT NULL,
        created_by VARCHAR(32),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        finished_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (id),
        FOREIGN KEY(plan_id) REFERENCES plans (id)
    )
    """)
    op.execute("CREATE INDEX ix_rollouts_created_at ON rollouts (created_at)")
    op.execute("CREATE INDEX ix_rollouts_plan_id ON rollouts (plan_id)")
    op.execute("CREATE INDEX ix_rollouts_release_id ON rollouts (release_id)")
    op.execute("CREATE INDEX ix_rollouts_status ON rollouts (status)")
    op.execute("""
    CREATE TABLE platform_releases (
        id VARCHAR(32) NOT NULL,
        application_id VARCHAR(32) NOT NULL,
        digest VARCHAR(64) NOT NULL,
        manifest JSON NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (application_id, digest),
        FOREIGN KEY(application_id) REFERENCES platform_applications (id)
    )
    """)
    op.execute("CREATE INDEX ix_platform_releases_application_id ON platform_releases (application_id)")
    op.execute("""
    CREATE TABLE platform_deployments (
        id VARCHAR(32) NOT NULL,
        project_id VARCHAR(32) NOT NULL,
        robot_id VARCHAR(32) NOT NULL,
        release_id VARCHAR(32) NOT NULL,
        generation BIGINT NOT NULL,
        state VARCHAR(24) NOT NULL,
        detail TEXT NOT NULL,
        observed_at TIMESTAMP WITH TIME ZONE,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (robot_id, generation),
        FOREIGN KEY(project_id) REFERENCES platform_projects (id),
        FOREIGN KEY(robot_id) REFERENCES platform_robots (id),
        FOREIGN KEY(release_id) REFERENCES platform_releases (id)
    )
    """)
    op.execute("CREATE INDEX ix_platform_deployments_project_id ON platform_deployments (project_id)")
    op.execute("CREATE INDEX ix_platform_deployments_robot_id ON platform_deployments (robot_id)")
    op.execute("""
    CREATE TABLE platform_missions (
        id VARCHAR(32) NOT NULL,
        project_id VARCHAR(32) NOT NULL,
        robot_id VARCHAR(32) NOT NULL,
        deployment_id VARCHAR(32) NOT NULL,
        release_id VARCHAR(32) NOT NULL,
        release_digest VARCHAR(64) NOT NULL,
        generation BIGINT NOT NULL,
        seed BIGINT NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        state VARCHAR(24) NOT NULL,
        identity JSON,
        "grant" TEXT,
        execution_started BOOLEAN NOT NULL,
        detail TEXT NOT NULL,
        terminal_digest VARCHAR(64),
        episode_id VARCHAR(32),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(project_id) REFERENCES platform_projects (id),
        FOREIGN KEY(robot_id) REFERENCES platform_robots (id),
        FOREIGN KEY(deployment_id) REFERENCES platform_deployments (id),
        FOREIGN KEY(release_id) REFERENCES platform_releases (id)
    )
    """)
    op.execute("CREATE INDEX ix_platform_missions_project_id ON platform_missions (project_id)")
    op.execute("CREATE INDEX ix_platform_missions_robot_id ON platform_missions (robot_id)")
    op.execute("""
    CREATE TABLE platform_episodes (
        id VARCHAR(32) NOT NULL,
        project_id VARCHAR(32) NOT NULL,
        mission_id VARCHAR(32) NOT NULL,
        release_digest VARCHAR(64) NOT NULL,
        state VARCHAR(24) NOT NULL,
        detail TEXT NOT NULL,
        summary JSON NOT NULL,
        identity JSON,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(project_id) REFERENCES platform_projects (id),
        UNIQUE (mission_id),
        FOREIGN KEY(mission_id) REFERENCES platform_missions (id)
    )
    """)
    op.execute("CREATE INDEX ix_platform_episodes_project_id ON platform_episodes (project_id)")


def downgrade() -> None:
    raise RuntimeError("Destructive PostgreSQL downgrade is unsupported; restore an isolated database")
