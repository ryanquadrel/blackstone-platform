"""Handle /halt, /resume, /status slash commands by short-circuiting Agno.

For these specific commands the LLM doesn't need to be involved — they
flip the auto_ship_halted single-row switch directly. The middleware
intercepts the webhook BEFORE Agno's handler, dispatches to the right
repo method, and replies via the Telegram Bot API.

Sits BEHIND the chat-id whitelist middleware (so unauthorized chats are
already filtered). Defensively re-checks the whitelist anyway.

Per memo §1.5.4: the Telegram /halt-auto-ship and /resume-auto-ship
commands flip the auto_ship_halted row. Telegram command names are
constrained to [a-z0-9_]+, so we expose them as /halt and /resume
(matching what BotFather setcommands accepted).
"""

from __future__ import annotations

import json
from typing import Awaitable, Callable, Iterable, Protocol

import aiohttp
from agno.utils.log import log_error, log_info, log_warning
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.state.halt_repo import HaltState

# SendMessage signature: (chat_id, text) -> awaitable returning HTTP status.
# Indirection so tests can inject a recorder.
SendMessageFn = Callable[[int, str], Awaitable[int]]

HANDLED_COMMANDS = {"/halt", "/resume", "/status"}


class HaltRepoProtocol(Protocol):
    """Structural type for the halt-state repo. Matches HaltStateRepo
    (the production async-psycopg implementation) and any test fake
    that exposes the same three async methods."""

    async def status(self) -> HaltState: ...
    async def halt(self, *, set_by: str, reason: str | None = None) -> HaltState: ...
    async def resume(self, *, set_by: str) -> HaltState: ...


class TelegramHaltCommandMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        *,
        repo: HaltRepoProtocol,
        send_message: SendMessageFn,
        allowed_chat_ids: Iterable[int],
        webhook_path_suffix: str = "/telegram/webhook",
    ) -> None:
        super().__init__(app)
        self.repo = repo
        self.send_message = send_message
        self.allowed_chat_ids = frozenset(int(c) for c in allowed_chat_ids)
        self.webhook_path_suffix = webhook_path_suffix

    async def dispatch(self, request: Request, call_next):
        if request.method != "POST" or not request.url.path.endswith(self.webhook_path_suffix):
            return await call_next(request)

        body_bytes = await request.body()

        try:
            body = json.loads(body_bytes)
        except json.JSONDecodeError:
            return await _replay(request, call_next, body_bytes)

        message = (body or {}).get("message") or (body or {}).get("edited_message")
        if not isinstance(message, dict):
            return await _replay(request, call_next, body_bytes)

        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            return await _replay(request, call_next, body_bytes)

        # Telegram commands look like "/halt", "/halt@bot_username",
        # or "/halt some reason text". Normalize to bare command + args.
        head, _, args = text.partition(" ")
        cmd = head.split("@", 1)[0].lower()

        if cmd not in HANDLED_COMMANDS:
            return await _replay(request, call_next, body_bytes)

        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not isinstance(chat_id, int) or chat_id not in self.allowed_chat_ids:
            # Whitelist middleware should have caught this. Defense in depth.
            log_warning(f"Halt-command middleware: dropping {cmd} from chat_id={chat_id}")
            return JSONResponse({"status": "dropped"}, status_code=200)

        from_user = (message.get("from") or {})
        set_by = f"telegram:{chat_id}:{from_user.get('username') or from_user.get('id', '?')}"

        try:
            if cmd == "/halt":
                state = await self.repo.halt(set_by=set_by, reason=args.strip() or None)
                reply = _format_halt_reply(state)
            elif cmd == "/resume":
                state = await self.repo.resume(set_by=set_by)
                reply = _format_resume_reply(state)
            else:  # /status
                state = await self.repo.status()
                reply = _format_status_reply(state)
        except Exception as e:
            log_error(f"Halt command {cmd} failed: {e!r}")
            reply = f"Command {cmd} failed: {type(e).__name__}. Check logs."

        log_info(f"Halt command {cmd} handled for chat_id={chat_id}; reply={reply!r}")
        try:
            await self.send_message(chat_id, reply)
        except Exception as e:
            # Reply failure is annoying but the DB update already landed.
            log_error(f"Failed to send Telegram reply for {cmd}: {e!r}")

        return JSONResponse({"status": "handled", "command": cmd}, status_code=200)


def _format_halt_reply(state: HaltState) -> str:
    lines = ["⏸ Auto-ship HALTED."]
    if state.reason:
        lines.append(f"Reason: {state.reason}")
    lines.append(f"Set by: {state.set_by}")
    return "\n".join(lines)


def _format_resume_reply(state: HaltState) -> str:
    return f"▶️ Auto-ship RESUMED.\nSet by: {state.set_by}"


def _format_status_reply(state: HaltState) -> str:
    head = "HALTED ⏸" if state.halted else "RUNNING ▶️"
    lines = [f"Auto-ship state: {head}"]
    if state.halted and state.reason:
        lines.append(f"Reason: {state.reason}")
    if state.set_by:
        lines.append(f"Last change by: {state.set_by}")
    lines.append(f"Last change at: {state.updated_at.isoformat()}")
    return "\n".join(lines)


async def _replay(request: Request, call_next, body_bytes: bytes) -> Response:
    """Re-feed an already-consumed body to the downstream handler."""

    async def receive():
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    request._receive = receive  # type: ignore[attr-defined]
    return await call_next(request)


# ---------------------------------------------------------------------------
# Default `send_message` implementation — POST to Telegram Bot API via aiohttp.
# Constructed in app/main.py with the bot token bound in.
# ---------------------------------------------------------------------------

def make_telegram_sender(bot_token: str) -> SendMessageFn:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    async def send(chat_id: int, text: str) -> int:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"chat_id": chat_id, "text": text}) as resp:
                return resp.status

    return send
