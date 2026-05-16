-- dispatches — every Detect→Improve→Hill Climb→Review→Merge stage attempt.
-- Memo: docs/auto-ship-platform-v2-customization-on-agno.md §1.5.1
--
-- Three CHECK constraints encode the lifecycle invariants:
--   • claim_owner and claim_expires_at are set together or not at all
--   • status='in_flight' requires a claim
--   • terminal_at is set iff status is terminal (success/failure/triage)
--
-- The unique (idempotency_key, stage, attempt_n) constraint is the dedupe
-- key — Detect computes idempotency_key from the (skill_name, evidence_hash)
-- pair so the same finding can't double-dispatch.
--
-- Idempotent — safe to re-run.

SET search_path TO auto_ship_platform;

CREATE TABLE IF NOT EXISTS dispatches (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key     TEXT NOT NULL,
    stage               TEXT NOT NULL CHECK (stage IN ('detect', 'improve', 'hill_climb', 'review', 'merge')),
    attempt_n           INT NOT NULL DEFAULT 1 CHECK (attempt_n >= 1),
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'in_flight', 'success', 'failure', 'triage')),
    claim_owner         TEXT,
    claim_expires_at    TIMESTAMPTZ,
    github_issue_id     INT,
    github_pr_id        INT,
    evidence_hash       TEXT NOT NULL,
    evidence_json       JSONB NOT NULL,
    parent_dispatch_id  UUID REFERENCES auto_ship_platform.dispatches(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    terminal_at         TIMESTAMPTZ,

    CONSTRAINT dispatch_attempt_unique
        UNIQUE (idempotency_key, stage, attempt_n),

    CONSTRAINT claim_columns_consistent
        CHECK ((claim_owner IS NULL) = (claim_expires_at IS NULL)),

    CONSTRAINT in_flight_requires_claim
        CHECK (status <> 'in_flight' OR claim_owner IS NOT NULL),

    CONSTRAINT terminal_at_consistent
        CHECK ((status IN ('success', 'failure', 'triage')) = (terminal_at IS NOT NULL))
);

-- Hot path for stage workers polling for work to claim.
CREATE INDEX IF NOT EXISTS dispatches_pending_by_stage
    ON dispatches (stage)
    WHERE status = 'pending';

-- Hot path for the lease-expiry sweep: in_flight rows past their TTL.
CREATE INDEX IF NOT EXISTS dispatches_in_flight_expired
    ON dispatches (claim_expires_at)
    WHERE status = 'in_flight';

COMMENT ON TABLE dispatches IS
    'Stage-attempt ledger. Every Detect/Improve/Hill Climb/Review/Merge attempt is one row. '
    'Lease via claim_owner + claim_expires_at; acquire SQL is in stage worker code, '
    'see memo §1.5.1 for the FOR UPDATE SKIP LOCKED pattern.';

COMMENT ON COLUMN dispatches.evidence_json IS
    'Full Finding evidence (JSONB). Per Codex round 2 P2: thin evidence_hash alone '
    'makes dedupe correctness undebuggable, so we keep the full payload here.';

COMMENT ON COLUMN dispatches.parent_dispatch_id IS
    'Lineage: an Improve dispatch references its parent Detect dispatch, etc. '
    'Used for per-PR cap enforcement at Detect-stage entry (memo §3.5).';
