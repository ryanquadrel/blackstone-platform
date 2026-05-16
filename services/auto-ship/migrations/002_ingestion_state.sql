-- ingestion_state + in_flight_runs — the two-tier ingestion of upstream
-- task_runs (which lives in blackstone-automations' Supabase).
--
-- Memo: docs/auto-ship-platform-v2-customization-on-agno.md §1.5.2
--
-- Why two tables:
--   • ingestion_state holds a single per-source cursor row with the
--     composite (started_at, id) high-water mark. Tie-tolerant cursor.
--   • in_flight_runs is the pending-resolution set: every task_run we've
--     ingested but not yet seen reach a terminal status. Detect feeds
--     only on resolved rows.
--
-- Upstream task_runs schema (from sql/016_task_runs.sql in
-- blackstone-automations) the Detect agent will ingest from:
--   • id BIGSERIAL PRIMARY KEY (the unique key)
--   • run_id UUID NOT NULL (NOT unique, kept here for trace correlation)
--   • started_at TIMESTAMPTZ NOT NULL DEFAULT now() (no created_at, no updated_at)
--   • status TEXT — running | success | error | skipped | timeout | crashed
--
-- Idempotent — safe to re-run.

SET search_path TO auto_ship_platform;

CREATE TABLE IF NOT EXISTS ingestion_state (
    source                  TEXT PRIMARY KEY,
    high_water_started_at   TIMESTAMPTZ NOT NULL,
    high_water_id           BIGINT NOT NULL,
    last_run_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE ingestion_state IS
    'Single-row-per-source cursor. For v1 the only source is task_runs. '
    'Composite (started_at, id) high-water tolerates same-timestamp ties.';

CREATE TABLE IF NOT EXISTS in_flight_runs (
    task_run_db_id          BIGINT PRIMARY KEY,
    task_run_uuid           UUID NOT NULL,
    skill_name              TEXT NOT NULL,
    first_seen_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_polled_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_observed_status    TEXT NOT NULL,
    resolved_at             TIMESTAMPTZ,

    -- Domain CHECK on the status string (Codex review PR#2): without this,
    -- typos persist forever as unresolved and Detect never sees those rows.
    -- Must include 'running' (the unresolved state) plus all terminal values.
    CONSTRAINT last_observed_status_valid
        CHECK (last_observed_status IN (
            'running',
            'success', 'error', 'skipped', 'timeout', 'crashed',
            'source_deleted', 'orphan_swept'
        )),

    CONSTRAINT resolved_at_consistent
        CHECK ((last_observed_status IN (
                    'success', 'error', 'skipped', 'timeout',
                    'crashed', 'source_deleted', 'orphan_swept'
                )) = (resolved_at IS NOT NULL))
);

COMMENT ON TABLE in_flight_runs IS
    'Per-task_run resolution tracker. A row stays here until the upstream '
    'task_run reaches a terminal status (success/error/skipped/timeout/crashed), '
    'is deleted upstream (source_deleted), or ages past the 24h orphan-sweep '
    'window (orphan_swept). Detect only consumes rows with resolved_at IS NOT NULL.';

COMMENT ON COLUMN in_flight_runs.task_run_db_id IS
    'References task_runs.id (BIGSERIAL — the unique key per upstream schema). '
    'NOT a foreign key because task_runs lives in a separate Supabase database; '
    'see memo §1.5.2 for the LEFT-JOIN-and-handle-NULL deletion handling.';

COMMENT ON COLUMN in_flight_runs.task_run_uuid IS
    'task_runs.run_id, kept for trace/log correlation only. '
    'NOT relied on as a key — upstream allows duplicate run_ids.';

-- Hot path for the in-flight sweep: every ingestion tick re-polls all
-- unresolved rows.
CREATE INDEX IF NOT EXISTS in_flight_runs_unresolved
    ON in_flight_runs (last_polled_at)
    WHERE resolved_at IS NULL;
