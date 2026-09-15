"""
Container healthcheck
=====================

Healthy means the API answers AND the configured Postgres accepts a login.

The previous check was `curl /agents`, which never touches the database, and
agno's storage routes swallow connection errors (GET /sessions returns 200 with
an empty page when Postgres rejects the password). A deployment could run for
weeks unable to reach its own storage while reporting healthy. Exits non-zero
on either failure so `docker ps` tells the truth.

Connection settings mirror db/url.py, including its defaults.
"""

import sys
import urllib.request
from os import getenv

import psycopg

API_URL = "http://localhost:8000/agents"


def check_api() -> str | None:
    try:
        with urllib.request.urlopen(API_URL, timeout=4) as response:
            if response.status != 200:
                return f"api returned HTTP {response.status}"
    except Exception as exc:
        return f"api probe failed: {type(exc).__name__}"
    return None


def check_db() -> str | None:
    try:
        psycopg.connect(
            host=getenv("DB_HOST", "localhost"),
            port=int(getenv("DB_PORT", "5432")),
            user=getenv("DB_USER", "ai"),
            password=getenv("DB_PASS", "ai"),
            dbname=getenv("DB_DATABASE", "ai"),
            connect_timeout=3,
        ).close()
    except Exception as exc:
        # Type only: the message can carry the host and role name.
        return f"database login failed: {type(exc).__name__}"
    return None


def main() -> int:
    failures = [msg for msg in (check_api(), check_db()) if msg]
    for msg in failures:
        print(f"healthcheck: {msg}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
