"""Tests for app.middleware.telegram_commands.

Use a fake repo + recording sender so the tests don't need a live DB or
network — both real implementations are exercised by the integration test
in app/state/test_halt_repo.py against the local agentos-db.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.middleware.telegram_commands import (
    TelegramHaltCommandMiddleware,
    _format_halt_reply,
    _format_resume_reply,
    _format_status_reply,
)
from app.state.halt_repo import HaltState

ALLOWED = 8510846692
BLOCKED = 999

_LAST_DOWNSTREAM_BODY: dict | None = None


async def _downstream(request: Request) -> JSONResponse:
    """Records what the downstream Agno handler would have seen.
    For /halt /resume /status the middleware should short-circuit BEFORE
    we get here. For everything else it should forward."""
    global _LAST_DOWNSTREAM_BODY
    try:
        _LAST_DOWNSTREAM_BODY = await request.json()
    except json.JSONDecodeError:
        _LAST_DOWNSTREAM_BODY = None
        return JSONResponse({"error": "bad json"}, status_code=400)
    return JSONResponse({"status": "downstream"})


@pytest.fixture(autouse=True)
def _reset_sentinel():
    global _LAST_DOWNSTREAM_BODY
    _LAST_DOWNSTREAM_BODY = None
    yield
    _LAST_DOWNSTREAM_BODY = None


class FakeRepo:
    """In-memory stand-in for HaltStateRepo."""

    def __init__(self) -> None:
        self.state = HaltState(
            halted=False,
            reason=None,
            set_by="migration:initial",
            updated_at=datetime(2026, 5, 16, tzinfo=timezone.utc),
        )
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def status(self) -> HaltState:
        self.calls.append(("status", {}))
        return self.state

    async def halt(self, *, set_by: str, reason: str | None = None) -> HaltState:
        self.calls.append(("halt", {"set_by": set_by, "reason": reason}))
        self.state = HaltState(
            halted=True,
            reason=reason,
            set_by=set_by,
            updated_at=datetime(2026, 5, 16, 1, tzinfo=timezone.utc),
        )
        return self.state

    async def resume(self, *, set_by: str) -> HaltState:
        self.calls.append(("resume", {"set_by": set_by}))
        self.state = HaltState(
            halted=False,
            reason=None,
            set_by=set_by,
            updated_at=datetime(2026, 5, 16, 2, tzinfo=timezone.utc),
        )
        return self.state


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def __call__(self, chat_id: int, text: str) -> int:
        self.sent.append((chat_id, text))
        return 200


@pytest.fixture
def repo() -> FakeRepo:
    return FakeRepo()


@pytest.fixture
def sender() -> RecordingSender:
    return RecordingSender()


@pytest.fixture
def client(repo: FakeRepo, sender: RecordingSender) -> TestClient:
    app = Starlette(routes=[Route("/telegram/webhook", _downstream, methods=["POST"])])
    app.add_middleware(
        TelegramHaltCommandMiddleware,
        repo=repo,
        send_message=sender,
        allowed_chat_ids={ALLOWED},
    )
    return TestClient(app)


def _msg(chat_id: int, text: str, username: str = "ryan") -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False, "first_name": "T", "username": username},
            "text": text,
            "date": 1700000000,
        },
    }


# ---------------------------------------------------------------------------
# Short-circuit behavior — handled commands stop at the middleware
# ---------------------------------------------------------------------------

def test_halt_short_circuits_and_flips_db(client: TestClient, repo: FakeRepo, sender: RecordingSender):
    r = client.post("/telegram/webhook", json=_msg(ALLOWED, "/halt"))
    assert r.status_code == 200
    assert r.json() == {"status": "handled", "command": "/halt"}
    assert _LAST_DOWNSTREAM_BODY is None, "downstream must not be called"
    assert repo.state.halted is True
    assert repo.state.reason is None
    assert repo.calls == [("halt", {"set_by": f"telegram:{ALLOWED}:ryan", "reason": None})]
    assert sender.sent == [(ALLOWED, _format_halt_reply(repo.state))]


def test_halt_with_reason_captures_text_after_command(client: TestClient, repo: FakeRepo):
    r = client.post("/telegram/webhook", json=_msg(ALLOWED, "/halt deploy underway, hold dispatch"))
    assert r.status_code == 200
    assert repo.state.reason == "deploy underway, hold dispatch"
    assert repo.calls[0][1]["reason"] == "deploy underway, hold dispatch"


def test_resume_short_circuits_and_flips_db(client: TestClient, repo: FakeRepo, sender: RecordingSender):
    # Pre-halt
    repo.state = HaltState(halted=True, reason="test", set_by="x", updated_at=datetime(2026, 5, 16, tzinfo=timezone.utc))
    r = client.post("/telegram/webhook", json=_msg(ALLOWED, "/resume"))
    assert r.status_code == 200
    assert repo.state.halted is False
    assert repo.state.reason is None
    assert sender.sent == [(ALLOWED, _format_resume_reply(repo.state))]


def test_status_reads_db_and_replies(client: TestClient, repo: FakeRepo, sender: RecordingSender):
    r = client.post("/telegram/webhook", json=_msg(ALLOWED, "/status"))
    assert r.status_code == 200
    assert repo.calls == [("status", {})]
    assert "Auto-ship state: RUNNING" in sender.sent[0][1]


def test_status_when_halted_includes_reason(client: TestClient, repo: FakeRepo, sender: RecordingSender):
    repo.state = HaltState(
        halted=True,
        reason="paused",
        set_by="x",
        updated_at=datetime(2026, 5, 16, tzinfo=timezone.utc),
    )
    client.post("/telegram/webhook", json=_msg(ALLOWED, "/status"))
    reply = sender.sent[0][1]
    assert "HALTED" in reply
    assert "paused" in reply


# ---------------------------------------------------------------------------
# Pass-through behavior — non-handled traffic forwards to downstream
# ---------------------------------------------------------------------------

def test_unhandled_command_forwards(client: TestClient, repo: FakeRepo):
    r = client.post("/telegram/webhook", json=_msg(ALLOWED, "/foo"))
    assert r.status_code == 200
    assert _LAST_DOWNSTREAM_BODY is not None, "/foo should reach downstream"
    assert _LAST_DOWNSTREAM_BODY["message"]["text"] == "/foo"
    assert repo.calls == []


def test_plain_text_forwards(client: TestClient, repo: FakeRepo):
    r = client.post("/telegram/webhook", json=_msg(ALLOWED, "hello bot"))
    assert r.status_code == 200
    assert _LAST_DOWNSTREAM_BODY is not None
    assert repo.calls == []


def test_command_with_at_username_still_handled(client: TestClient, repo: FakeRepo):
    """Telegram appends @bot_username when commands fire in groups."""
    r = client.post("/telegram/webhook", json=_msg(ALLOWED, "/halt@blackstone_autoship_bot real reason"))
    assert r.status_code == 200
    assert repo.state.halted is True
    assert repo.state.reason == "real reason"


# ---------------------------------------------------------------------------
# Defense-in-depth — chat-id check
# ---------------------------------------------------------------------------

def test_handled_command_from_blocked_chat_dropped(client: TestClient, repo: FakeRepo, sender: RecordingSender):
    """Chat-whitelist middleware should already block this; defense in depth."""
    r = client.post("/telegram/webhook", json=_msg(BLOCKED, "/halt"))
    assert r.status_code == 200
    assert r.json() == {"status": "dropped"}
    assert repo.calls == []
    assert sender.sent == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_repo_failure_replies_with_error_message(client: TestClient, sender: RecordingSender):
    """If the DB call raises, send an error reply rather than silently 500."""

    class ExplodingRepo:
        async def status(self):
            raise RuntimeError("db down")

        async def halt(self, **kwargs):
            raise RuntimeError("db down")

        async def resume(self, **kwargs):
            raise RuntimeError("db down")

    app = Starlette(routes=[Route("/telegram/webhook", _downstream, methods=["POST"])])
    app.add_middleware(
        TelegramHaltCommandMiddleware,
        repo=ExplodingRepo(),
        send_message=sender,
        allowed_chat_ids={ALLOWED},
    )
    c = TestClient(app)
    r = c.post("/telegram/webhook", json=_msg(ALLOWED, "/halt"))
    assert r.status_code == 200
    assert sender.sent
    assert "failed" in sender.sent[0][1].lower()


def test_send_failure_does_not_break_request(client: TestClient, repo: FakeRepo):
    """Telegram API errors are logged but don't fail the webhook (the DB
    update already landed)."""

    async def angry_sender(chat_id: int, text: str) -> int:
        raise RuntimeError("network down")

    app = Starlette(routes=[Route("/telegram/webhook", _downstream, methods=["POST"])])
    app.add_middleware(
        TelegramHaltCommandMiddleware,
        repo=repo,
        send_message=angry_sender,
        allowed_chat_ids={ALLOWED},
    )
    c = TestClient(app)
    r = c.post("/telegram/webhook", json=_msg(ALLOWED, "/halt"))
    assert r.status_code == 200
    assert repo.state.halted is True


def test_format_helpers_render_expected_strings():
    """Smoke-check the formatter functions in isolation so changes to copy
    show up as test diffs."""
    s = HaltState(
        halted=True,
        reason="testing",
        set_by="telegram:1:ryan",
        updated_at=datetime(2026, 5, 16, tzinfo=timezone.utc),
    )
    assert "HALTED" in _format_halt_reply(s)
    assert "testing" in _format_halt_reply(s)
    assert "telegram:1:ryan" in _format_halt_reply(s)
    assert "RESUMED" in _format_resume_reply(s)
    assert "HALTED" in _format_status_reply(s)
    assert "testing" in _format_status_reply(s)
