"""Read/write the auto_ship_halted single-row halt switch.

Schema lives in services/auto-ship/migrations/003_auto_ship_halted.sql.
The table has exactly one row (`id BOOLEAN PRIMARY KEY DEFAULT TRUE
CHECK (id = TRUE)`); we read/update WHERE id = TRUE.

Backed by an async psycopg connection so it composes with FastAPI
handlers without blocking the event loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import psycopg


@dataclass(frozen=True)
class HaltState:
    halted: bool
    reason: str | None
    set_by: str | None
    updated_at: datetime


class HaltStateRepo:
    """Async accessor for auto_ship_platform.auto_ship_halted.

    The repo holds a libpq conninfo string and opens a fresh connection
    per call. Halt/resume traffic is rare (manual Telegram commands), so
    pooling is not worth the complexity for v1.
    """

    def __init__(self, conn_kwargs: dict) -> None:
        self._conn_kwargs = conn_kwargs

    async def status(self) -> HaltState:
        async with await psycopg.AsyncConnection.connect(**self._conn_kwargs) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT halted, reason, set_by, updated_at FROM auto_ship_platform.auto_ship_halted WHERE id = TRUE"
                )
                row = await cur.fetchone()
        if row is None:
            raise RuntimeError("auto_ship_halted seed row missing — re-run services/auto-ship/migrations/apply.py")
        halted, reason, set_by, updated_at = row
        return HaltState(halted=halted, reason=reason, set_by=set_by, updated_at=updated_at)

    async def halt(self, *, set_by: str, reason: str | None = None) -> HaltState:
        async with await psycopg.AsyncConnection.connect(**self._conn_kwargs) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE auto_ship_platform.auto_ship_halted "
                    "SET halted = TRUE, reason = %s, set_by = %s, updated_at = now() "
                    "WHERE id = TRUE "
                    "RETURNING halted, reason, set_by, updated_at",
                    (reason, set_by),
                )
                row = await cur.fetchone()
            await conn.commit()
        return HaltState(*row)  # type: ignore[misc]

    async def resume(self, *, set_by: str) -> HaltState:
        async with await psycopg.AsyncConnection.connect(**self._conn_kwargs) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE auto_ship_platform.auto_ship_halted "
                    "SET halted = FALSE, reason = NULL, set_by = %s, updated_at = now() "
                    "WHERE id = TRUE "
                    "RETURNING halted, reason, set_by, updated_at",
                    (set_by,),
                )
                row = await cur.fetchone()
            await conn.commit()
        return HaltState(*row)  # type: ignore[misc]
