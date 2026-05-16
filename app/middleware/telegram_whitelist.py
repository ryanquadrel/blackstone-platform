"""Telegram chat-id whitelist middleware.

Agno's Telegram interface (agno.os.interfaces.telegram) accepts updates from
*any* chat that finds the bot's username. For the auto-ship platform we
need a hard restriction: only the firm-controlled chats may issue commands.

Sits ahead of Agno's `/telegram/webhook` POST handler. For unwhitelisted
chat_ids it returns HTTP 200 (Telegram considers the update delivered and
will not retry) without forwarding to Agno.

ADR: docs/decisions/0001-telegram-two-bots.md (the new dedicated bot exists
specifically because Agno's webhook handler doesn't filter by chat_id).
"""

from __future__ import annotations

import json
from typing import Iterable

from agno.utils.log import log_info, log_warning
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp


class TelegramChatWhitelistMiddleware(BaseHTTPMiddleware):
    """Drop Telegram webhook updates from non-whitelisted chats.

    Args:
        app: the Starlette/FastAPI app
        allowed_chat_ids: integer chat ids permitted to interact with the
            bot. Empty set = block everything (defensive default).
        webhook_path_suffix: which path to filter. Default matches Agno's
            Telegram interface mount (`/telegram/webhook`); change if the
            interface is mounted with a different prefix.
    """

    def __init__(
        self,
        app: ASGIApp,
        allowed_chat_ids: Iterable[int],
        webhook_path_suffix: str = "/telegram/webhook",
    ) -> None:
        super().__init__(app)
        self.allowed_chat_ids = frozenset(int(c) for c in allowed_chat_ids)
        self.webhook_path_suffix = webhook_path_suffix

    async def dispatch(self, request: Request, call_next):
        # Pass non-webhook traffic through unchanged.
        if request.method != "POST" or not request.url.path.endswith(self.webhook_path_suffix):
            return await call_next(request)

        # Read the body once. We need to replay it to the downstream handler
        # because Starlette's request.body() consumes the underlying stream.
        body_bytes = await request.body()

        chat_id = _extract_chat_id(body_bytes)

        if chat_id is None:
            # Couldn't extract a chat_id — could be a callback_query or
            # malformed JSON. Forward and let Agno (or its 400) handle it.
            return await _replay(request, call_next, body_bytes)

        if chat_id not in self.allowed_chat_ids:
            log_warning(f"Dropping Telegram update from non-whitelisted chat_id={chat_id}")
            # 200 with the standard Agno-style payload makes Telegram treat
            # it as delivered (no retries). Silent on the bot side.
            return JSONResponse({"status": "dropped"}, status_code=200)

        log_info(f"Telegram update from whitelisted chat_id={chat_id}")
        return await _replay(request, call_next, body_bytes)


def _extract_chat_id(body_bytes: bytes) -> int | None:
    """Pull chat.id out of a Telegram update payload.

    Returns None for any shape we don't recognize (callback_query,
    inline_query, edited_channel_post, …) — caller decides forwarding."""
    try:
        body = json.loads(body_bytes)
    except json.JSONDecodeError:
        return None
    if not isinstance(body, dict):
        return None
    # Agno's webhook handler inspects message + edited_message; mirror that.
    message = body.get("message") or body.get("edited_message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None
    chat_id = chat.get("id")
    return int(chat_id) if isinstance(chat_id, int) else None


async def _replay(request: Request, call_next, body_bytes: bytes) -> Response:
    """Re-feed an already-consumed body to the downstream handler."""

    async def receive():
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    request._receive = receive  # type: ignore[attr-defined]
    return await call_next(request)
