"""Integration tests for HaltStateRepo against a real Postgres.

Skipped automatically when the local agentos-db isn't reachable (CI
without a DB service). When the env vars point at a database with the
auto_ship_platform schema applied, the suite exercises status/halt/
resume round-trips and asserts state reflects the SQL.

Restores the seed `(halted=FALSE, reason=NULL, set_by='migration:initial')`
state on teardown so re-running this file doesn't leave the local DB in
a halted state.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import psycopg
import pytest

from app.state.halt_repo import HaltStateRepo

CONN: dict[str, Any] = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": os.environ.get("DB_PORT", "5432"),
    "user": os.environ.get("DB_USER", "ai"),
    "password": os.environ.get("DB_PASS", "ai"),
    "dbname": os.environ.get("DB_DATABASE", "ai"),
}


def _have_db() -> bool:
    """Probe with a short-timeout connect. Skip the suite if unreachable."""
    try:
        with psycopg.connect(**CONN, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM auto_ship_platform.auto_ship_halted WHERE id = TRUE")
                return cur.fetchone() is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _have_db(),
    reason="auto_ship_platform schema not reachable; integration test skipped",
)


@pytest.fixture
def repo() -> HaltStateRepo:
    return HaltStateRepo(conn_kwargs=CONN)


def _reset_seed():
    with psycopg.connect(**CONN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE auto_ship_platform.auto_ship_halted "
                "SET halted = FALSE, reason = NULL, set_by = 'migration:initial', "
                "    updated_at = now() "
                "WHERE id = TRUE"
            )
        conn.commit()


@pytest.fixture(autouse=True)
def _seed_around_test():
    """Reset seed BEFORE and AFTER each test so a leftover from a prior
    smoke run (or a sibling test) doesn't leak in. The migration table
    is a singleton row — concurrency is not a concern at v1 scale."""
    _reset_seed()
    yield
    _reset_seed()


def test_status_returns_seed_when_clean(repo: HaltStateRepo):
    state = asyncio.run(repo.status())
    assert state.halted is False
    assert state.set_by == "migration:initial"
    assert state.reason is None


def test_halt_then_resume_round_trip(repo: HaltStateRepo):
    halted = asyncio.run(repo.halt(set_by="test:halt", reason="integration test"))
    assert halted.halted is True
    assert halted.reason == "integration test"
    assert halted.set_by == "test:halt"

    status = asyncio.run(repo.status())
    assert status.halted is True

    resumed = asyncio.run(repo.resume(set_by="test:resume"))
    assert resumed.halted is False
    assert resumed.reason is None
    assert resumed.set_by == "test:resume"


def test_halt_with_no_reason(repo: HaltStateRepo):
    state = asyncio.run(repo.halt(set_by="test:no-reason"))
    assert state.halted is True
    assert state.reason is None
