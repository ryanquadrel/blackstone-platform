# Auto-ship platform — state-layer migrations

Per memo §1.5 ([`docs/auto-ship-platform-v2-customization-on-agno.md`](https://github.com/ryanquadrel/blackstone-automations/blob/main/docs/auto-ship-platform-v2-customization-on-agno.md)). Four idempotent SQL files, applied in lexical order.

## Files

| File | Memo § | What it creates |
|---|---|---|
| `000_schema.sql` | (preamble) | `auto_ship_platform` schema + `pgcrypto` extension. |
| `001_dispatches.sql` | §1.5.1 | `dispatches` table + 2 partial indexes. The stage-attempt ledger; lease via `claim_owner` + `claim_expires_at`. |
| `002_ingestion_state.sql` | §1.5.2 | `ingestion_state` (single-row cursor) + `in_flight_runs` + 1 partial index. The two-tier ingestion of upstream `task_runs`. |
| `003_auto_ship_halted.sql` | §1.5.4 | `auto_ship_halted` (single-row halt switch) + initial seed row. |

All migrations are scoped to the `auto_ship_platform` schema; they don't touch Agno's `agno_*` tables in `public`.

## Applying

```bash
# Local agentos-db (no env needed — uses the same defaults as db/url.py).
python services/auto-ship/migrations/apply.py

# Supabase pxyrurfpeyodesjxjfqc — set env then apply.
DB_HOST=db.pxyrurfpeyodesjxjfqc.supabase.co \
DB_USER=postgres \
DB_PASS=$(cat ~/.blackstone-secrets/supabase-pxyr-db-password.txt) \
DB_DATABASE=postgres \
python services/auto-ship/migrations/apply.py
```

The applier runs each file in its own transaction. All four files are idempotent (`CREATE … IF NOT EXISTS`, `ON CONFLICT DO NOTHING` on the seed row), so re-running is safe.

## What the schema doesn't include

Per memo §1.5.5, deliberately left out of v1:

- ❌ `auto_ship_findings` separate from `dispatches` — finding evidence is folded into `dispatches.evidence_json`
- ❌ Multiple enum types — TEXT with explicit `CHECK` constraints
- ❌ Cost columns inside `dispatches` — read from `task_runs.usd_cost` via `in_flight_runs` join
- ❌ Heartbeat protocol — TTL-longer-than-stage-wall-clock + per-side-effect claim-loss check
- ❌ Cross-stage transition counter — per-PR cap enforced via lineage walk through `parent_dispatch_id`
- ❌ Tier checkpoint protocol — static-tier v1 (memo §3.4) sidesteps this

## The acquire / finish SQL contract

Lives in stage-worker code, not in these migrations, but documented here so the schema's intent is visible. Per memo §1.5.1:

```sql
-- Acquire (in stage worker startup)
WITH candidate AS (
    SELECT id FROM auto_ship_platform.dispatches
     WHERE stage = :stage
       AND (status = 'pending'
            OR (status = 'in_flight' AND claim_expires_at < now()))
     ORDER BY created_at
     FOR UPDATE SKIP LOCKED
     LIMIT 1
)
UPDATE auto_ship_platform.dispatches d
   SET status = 'in_flight',
       claim_owner = :worker_run_id,
       claim_expires_at = now() + INTERVAL '15 minutes'
  FROM candidate
 WHERE d.id = candidate.id
RETURNING d.*;

-- Finish (terminal update guarded by ownership + status)
UPDATE auto_ship_platform.dispatches
   SET status = :final_status,
       terminal_at = now(),
       claim_owner = NULL,
       claim_expires_at = NULL,
       github_issue_id = COALESCE(:issue_id, github_issue_id),
       github_pr_id = COALESCE(:pr_id, github_pr_id)
 WHERE id = :id
   AND claim_owner = :worker_run_id
   AND status = 'in_flight'
RETURNING id;
-- 0 rows = lost the claim. Worker MUST stop without further side effects.
```

Lease TTL is 15 min; stage hard wall-clock is 10 min (worker SIGKILLed at 10 regardless). Workers must re-check `claim_owner` before each GitHub side effect.
