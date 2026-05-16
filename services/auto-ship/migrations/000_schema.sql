-- Auto-ship platform schema namespace.
--
-- All Blackstone-owned state-layer tables (dispatches, ingestion_state,
-- in_flight_runs, auto_ship_halted) live under this schema. Agno's own
-- runtime tables (agno_*) sit under the default `public` schema and are
-- managed by Agno's auto-migration in db/session.py.
--
-- Idempotent — safe to re-run.

CREATE SCHEMA IF NOT EXISTS auto_ship_platform;

-- gen_random_uuid() comes from pgcrypto and is required by 001_dispatches.sql.
-- The agnohq/pgvector image has pgcrypto available; this CREATE EXTENSION is
-- idempotent if it's already loaded.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

COMMENT ON SCHEMA auto_ship_platform IS
  'State layer for the Blackstone auto-ship platform. Tables here are owned by '
  'the customization layer, NOT inherited from upstream agnohq template. See '
  'docs/auto-ship-platform-v2-customization-on-agno.md §1.5.';
