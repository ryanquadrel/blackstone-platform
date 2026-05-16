"""
AgentOS Entrypoint
==================
"""

from contextlib import asynccontextmanager
from os import getenv
from pathlib import Path

from agno.os import AgentOS
from agno.utils.log import log_info

from agents.code_search import code_search
from agents.web_search import web_search
from db import get_postgres_db

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
runtime_env = getenv("RUNTIME_ENV", "prd")
scheduler_base_url = getenv("AGENTOS_URL", "http://127.0.0.1:8000")

# ---------------------------------------------------------------------------
# Interfaces
# - CodeSearch on Slack — both env vars must be set
# - CodeSearch on Telegram — TELEGRAM_TOKEN + TELEGRAM_ALLOWED_CHAT_IDS;
#   per ADR docs/decisions/0001-telegram-two-bots.md the platform uses a
#   dedicated bot, NOT the existing notification bot. Chat-id whitelist
#   middleware is added below the agent_os.get_app() call so unauthorized
#   chats can't talk to the agent at all (Agno's interface itself does NOT
#   filter by chat_id).
# ---------------------------------------------------------------------------
SLACK_BOT_TOKEN = getenv("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = getenv("SLACK_SIGNING_SECRET", "")
TELEGRAM_TOKEN = getenv("TELEGRAM_TOKEN", "")
TELEGRAM_ALLOWED_CHAT_IDS = getenv("TELEGRAM_ALLOWED_CHAT_IDS", "")

interfaces: list = []
if SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET:
    from agno.os.interfaces.slack import Slack

    interfaces.append(
        Slack(
            agent=code_search,
            streaming=True,
            token=SLACK_BOT_TOKEN,
            signing_secret=SLACK_SIGNING_SECRET,
            resolve_user_identity=True,
        )
    )

# Parse once, drive both append-interface AND install-middleware below
# off the same _telegram_allowed_chat_ids signal so they cannot drift
# (Codex PR#4 P3). Parser raises on token-set-but-empty-whitelist.
from app.middleware.telegram_whitelist import parse_allowed_chat_ids  # noqa: E402

_telegram_allowed_chat_ids: set[int] = parse_allowed_chat_ids(TELEGRAM_TOKEN, TELEGRAM_ALLOWED_CHAT_IDS)
if _telegram_allowed_chat_ids:
    from agno.os.interfaces.telegram import Telegram

    interfaces.append(Telegram(agent=code_search, token=TELEGRAM_TOKEN))


# ---------------------------------------------------------------------------
# Lifespan — extension hook for app-level startup / teardown.
#
# AgentOS handles the MCP lifecycle (connect on startup, close on shutdown).
# Keep this hook in place so you can plug in your own setup as needed.
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app):  # type: ignore[no-untyped-def]
    log_info("AgentOS lifespan: startup")
    try:
        yield
    finally:
        log_info("AgentOS lifespan: shutdown")


# ---------------------------------------------------------------------------
# Create AgentOS
# ---------------------------------------------------------------------------
agent_os = AgentOS(
    name="AgentOS",
    tracing=True,
    scheduler=True,
    scheduler_base_url=scheduler_base_url,
    # JWT auth disabled. The platform is internal infrastructure:
    #  - /telegram/webhook is gated by the chat-id whitelist middleware
    #    AND Agno's webhook-secret token (TELEGRAM_WEBHOOK_SECRET_TOKEN).
    #  - /agents HTTP endpoints are LAN-internal (EdgeXpert is not public).
    # Upstream's runtime_env=="prd" gate is reasonable for SaaS deploys
    # where /agents is publicly reachable; for our LAN deploy it would
    # require generating + maintaining a JWT keypair at os.agno.com for
    # no security benefit. Reverse this if the platform ever fronts
    # public traffic.
    authorization=False,
    lifespan=lifespan,
    db=get_postgres_db(),
    agents=[web_search, code_search],
    interfaces=interfaces,
    config=str(Path(__file__).parent / "config.yaml"),
)
app = agent_os.get_app()

# Telegram middleware stack. Codex PR#4 P3 + Step 5c.
#
# Same _telegram_allowed_chat_ids signal as the interface append above
# drives BOTH middlewares — if the interface was appended, both middlewares
# are installed; the parser raises on the in-between state where token is
# set but chat-id set is empty.
#
# Starlette runs middlewares in reverse order of add_middleware() — the
# LAST add wraps everything, so it runs FIRST. Desired flow:
#   1. Whitelist middleware  → drop if chat_id not in allowlist
#   2. Halt-command middleware → handle /halt /resume /status (no LLM call)
#   3. Agno's /telegram/webhook → everything else (LLM-backed agent reply)
# Therefore: command middleware added FIRST (becomes inner), whitelist
# SECOND (becomes outer).
if _telegram_allowed_chat_ids:
    from app.middleware.telegram_commands import (
        TelegramHaltCommandMiddleware,
        make_telegram_sender,
    )
    from app.middleware.telegram_whitelist import TelegramChatWhitelistMiddleware
    from app.state.halt_repo import HaltStateRepo
    from db.url import db_url as _db_url_str

    # Reuse the same DB env contract as Agno's PostgresDb. We parse via
    # psycopg.conninfo so tests don't have to maintain a parallel set.
    from psycopg.conninfo import conninfo_to_dict

    # db_url is built with the SQLAlchemy "postgresql+psycopg://" prefix;
    # strip the dialect for psycopg's libpq parser.
    _libpq_url = _db_url_str.replace("postgresql+psycopg://", "postgresql://", 1)
    _conn_kwargs = conninfo_to_dict(_libpq_url)

    app.add_middleware(
        TelegramHaltCommandMiddleware,
        repo=HaltStateRepo(conn_kwargs=_conn_kwargs),
        send_message=make_telegram_sender(TELEGRAM_TOKEN),
        allowed_chat_ids=_telegram_allowed_chat_ids,
    )
    app.add_middleware(
        TelegramChatWhitelistMiddleware,
        allowed_chat_ids=_telegram_allowed_chat_ids,
    )


if __name__ == "__main__":
    agent_os.serve(app="app.main:app", reload=runtime_env == "dev")
