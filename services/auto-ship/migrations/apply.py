"""Apply auto-ship platform migrations to a Postgres target.

Sister script to db/url.py — uses the same env-var contract so applying
to the local agentos-db works without touching the file:

    python services/auto-ship/migrations/apply.py

To apply against Supabase or any other Postgres, override the connection
via env (matches db/url.py defaults):

    DB_HOST=db.<ref>.supabase.co \\
    DB_USER=postgres \\
    DB_PASS=$(cat ~/.blackstone-secrets/supabase-pxyr-db-password.txt) \\
    DB_DATABASE=postgres \\
    python services/auto-ship/migrations/apply.py

Migrations are applied in lexical order (000_*.sql, 001_*.sql, ...) and
each file is executed in its own transaction. All four migrations are
idempotent — re-running is safe.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent
DEFAULTS = {
    "DB_HOST": "localhost",
    "DB_PORT": "5432",
    "DB_USER": "ai",
    "DB_PASS": "ai",
    "DB_DATABASE": "ai",
}


def _conninfo() -> str:
    parts = {key: os.environ.get(key, default) for key, default in DEFAULTS.items()}
    return (
        f"host={parts['DB_HOST']} port={parts['DB_PORT']} "
        f"user={parts['DB_USER']} password={parts['DB_PASS']} "
        f"dbname={parts['DB_DATABASE']}"
    )


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))


def main() -> int:
    files = _migration_files()
    if not files:
        print(f"No migrations found in {MIGRATIONS_DIR}", file=sys.stderr)
        return 1

    print(
        f"Applying {len(files)} migration(s) to {DEFAULTS['DB_HOST']}=>{os.environ.get('DB_HOST', DEFAULTS['DB_HOST'])} ..."
    )

    with psycopg.connect(_conninfo()) as conn:
        for path in files:
            print(f"  {path.name} ... ", end="", flush=True)
            sql = path.read_text(encoding="utf-8")
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
            print("ok")

    print("All migrations applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
