"""Tests for app.middleware.telegram_whitelist.

Build a tiny Starlette app with a sentinel POST /telegram/webhook handler
that records what it received, mount the middleware, then fire requests
that mimic real Telegram update payloads.
"""

from __future__ import annotations

import json

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.middleware.telegram_whitelist import (
    TelegramChatWhitelistMiddleware,
    parse_allowed_chat_ids,
)

ALLOWED = 8510846692
BLOCKED = 1234567890

# Sentinel keeps the last body the downstream handler saw so tests can
# assert "downstream was called with X" / "downstream was not called".
_LAST_BODY: dict | None = None


async def _downstream(request: Request) -> JSONResponse:
    global _LAST_BODY
    try:
        _LAST_BODY = await request.json()
    except json.JSONDecodeError:
        _LAST_BODY = None
        return JSONResponse({"error": "bad json"}, status_code=400)
    return JSONResponse({"status": "ok"})


@pytest.fixture(autouse=True)
def _reset_sentinel():
    global _LAST_BODY
    _LAST_BODY = None
    yield
    _LAST_BODY = None


@pytest.fixture
def client() -> TestClient:
    app = Starlette(routes=[Route("/telegram/webhook", _downstream, methods=["POST"])])
    app.add_middleware(TelegramChatWhitelistMiddleware, allowed_chat_ids={ALLOWED})
    return TestClient(app)


def _update(chat_id: int, text: str = "hi") -> dict:
    """Synthesize a plausible Telegram update body."""
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False, "first_name": "Tester"},
            "text": text,
            "date": 1700000000,
        },
    }


def test_whitelisted_chat_passes_through(client: TestClient):
    r = client.post("/telegram/webhook", json=_update(ALLOWED, "hello"))
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert _LAST_BODY is not None
    assert _LAST_BODY["message"]["chat"]["id"] == ALLOWED
    assert _LAST_BODY["message"]["text"] == "hello"


def test_non_whitelisted_chat_dropped(client: TestClient):
    r = client.post("/telegram/webhook", json=_update(BLOCKED, "spam"))
    assert r.status_code == 200, "Telegram-friendly drop returns 200, not 403"
    assert r.json() == {"status": "dropped"}
    assert _LAST_BODY is None, "downstream must NOT have been called"


def test_edited_message_uses_same_chat_id_path(client: TestClient):
    body = {
        "update_id": 2,
        "edited_message": {
            "message_id": 5,
            "chat": {"id": ALLOWED, "type": "private"},
            "text": "edited",
            "date": 1700000001,
            "edit_date": 1700000002,
        },
    }
    r = client.post("/telegram/webhook", json=body)
    assert r.status_code == 200
    assert _LAST_BODY is not None and _LAST_BODY["edited_message"]["chat"]["id"] == ALLOWED


def test_callback_query_without_message_chat_dropped(client: TestClient):
    """Updates without `message.chat` (callback_query, channel_post,
    inline_query, …) carry no extractable chat_id. Per Codex PR#4 P2 we
    fail-closed and drop rather than forwarding — a whitelist boundary
    shouldn't depend on downstream staying narrow."""
    body = {
        "update_id": 3,
        "callback_query": {
            "id": "cb1",
            "from": {"id": BLOCKED, "is_bot": False, "first_name": "Stranger"},
            "data": "foo",
        },
    }
    r = client.post("/telegram/webhook", json=body)
    assert r.status_code == 200
    assert r.json() == {"status": "dropped"}
    assert _LAST_BODY is None, "downstream must NOT be called on unknown shapes"


def test_malformed_json_dropped(client: TestClient):
    """Garbage body — middleware can't extract chat_id, so we drop
    (fail-closed). Earlier behavior forwarded to let downstream 400, but
    Codex PR#4 P2 pointed out that a whitelist boundary shouldn't depend
    on downstream's error behavior to enforce security."""
    r = client.post(
        "/telegram/webhook",
        content=b"not json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json() == {"status": "dropped"}
    assert _LAST_BODY is None


def test_non_webhook_path_unaffected(client: TestClient):
    """Other paths bypass the middleware entirely."""
    r = client.post("/telegram/webhook/extra", json=_update(BLOCKED))
    # Path doesn't end with the suffix — middleware ignores it; the
    # Starlette router has no matching route → 405/404. Either way,
    # the middleware did not itself drop or forward.
    assert r.status_code in (404, 405)


def test_get_request_unaffected(client: TestClient):
    """Non-POST methods bypass the filter."""
    r = client.get("/telegram/webhook")
    # Route is POST-only → 405. Middleware should not interfere.
    assert r.status_code == 405


def test_empty_allowed_set_blocks_everything():
    """Defensive default: empty whitelist drops every chat."""
    app = Starlette(routes=[Route("/telegram/webhook", _downstream, methods=["POST"])])
    app.add_middleware(TelegramChatWhitelistMiddleware, allowed_chat_ids=set())
    c = TestClient(app)
    r = c.post("/telegram/webhook", json=_update(ALLOWED))
    assert r.status_code == 200
    assert r.json() == {"status": "dropped"}
    assert _LAST_BODY is None


# ---------------------------------------------------------------------------
# parse_allowed_chat_ids — env-string parser
# Codex PR#4 P1 + P3 — every degenerate input that would have slipped
# past the original `.strip()` check must now raise.
# ---------------------------------------------------------------------------


def test_parse_no_token_returns_empty_set():
    """Telegram interface stays off when TELEGRAM_TOKEN isn't set."""
    assert parse_allowed_chat_ids("", "8510846692") == set()
    assert parse_allowed_chat_ids("", "") == set()


def test_parse_happy_path_single_id():
    assert parse_allowed_chat_ids("tok", "8510846692") == {8510846692}


def test_parse_happy_path_multiple_ids():
    assert parse_allowed_chat_ids("tok", "1,2,3") == {1, 2, 3}
    assert parse_allowed_chat_ids("tok", " 1 , 2 , 3 ") == {1, 2, 3}


@pytest.mark.parametrize(
    "degenerate",
    [
        "",  # the obvious case
        " ",  # whitespace only
        ",",  # the Codex P1 — comma-only passes .strip() but parses empty
        ",,,",
        " , , ",  # whitespace + commas
        "\t\n",
    ],
)
def test_parse_token_set_but_empty_chat_ids_raises(degenerate: str):
    """Token-set-but-no-real-chat-ids is fail-closed."""
    with pytest.raises(RuntimeError, match="TELEGRAM_ALLOWED_CHAT_IDS"):
        parse_allowed_chat_ids("tok", degenerate)


def test_parse_invalid_int_raises():
    """Non-integer entries are a config error, not a silent skip."""
    with pytest.raises(ValueError):
        parse_allowed_chat_ids("tok", "1,not-a-number,3")
