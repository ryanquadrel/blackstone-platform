-- auto_ship_halted — single-row halt switch flipped by the Telegram
-- /halt-auto-ship and /resume-auto-ship commands.
--
-- Memo: docs/auto-ship-platform-v2-customization-on-agno.md §1.5.4
--
-- Every Detect cycle and stage worker reads this row at start. If
-- halted=TRUE the worker exits immediately without doing any side effect.
--
-- The id=TRUE PRIMARY KEY trick enforces single-row semantics — there
-- can never be two rows in this table.
--
-- Idempotent — safe to re-run (the seed UPSERT preserves the live value).

SET search_path TO auto_ship_platform;

CREATE TABLE IF NOT EXISTS auto_ship_halted (
    id          BOOLEAN PRIMARY KEY DEFAULT TRUE
                CHECK (id = TRUE),                      -- enforces single row
    halted      BOOLEAN NOT NULL DEFAULT FALSE,
    reason      TEXT,
    set_by      TEXT,                                   -- 'telegram:<chat_id>' or 'cli:<user>' etc.
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed the single row if it doesn't exist. ON CONFLICT DO NOTHING keeps
-- any existing live state intact across migration re-runs.
INSERT INTO auto_ship_halted (id, halted, reason, set_by)
VALUES (TRUE, FALSE, NULL, 'migration:initial')
ON CONFLICT (id) DO NOTHING;

COMMENT ON TABLE auto_ship_halted IS
    'Single-row halt switch. Telegram /halt-auto-ship sets halted=TRUE; '
    '/resume-auto-ship sets halted=FALSE. Detect + every stage worker '
    'reads this at start and exits without side effects when halted.';

COMMENT ON COLUMN auto_ship_halted.id IS
    'Always TRUE. The CHECK + PRIMARY KEY combination enforces that this '
    'table can only ever hold one row. Read with: SELECT halted, reason, '
    'set_by FROM auto_ship_halted WHERE id = TRUE;';
