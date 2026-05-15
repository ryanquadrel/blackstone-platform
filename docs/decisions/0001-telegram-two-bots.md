# ADR 0001 — Telegram interface uses a dedicated bot, not the existing @blackstone bot

**Date:** 2026-05-15
**Status:** Accepted
**Context:** Customization of [agno-agi/agent-platform-railway](https://github.com/agno-agi/agent-platform-railway) for Blackstone Law's auto-ship platform. Supersedes the original Step 5 framing in [blackstone-automations PR #176](https://github.com/ryanquadrel/blackstone-automations/pull/176).

## Decision

Auto-ship platform's Telegram interface runs on a **new, dedicated @BotFather bot** (working name: `@blackstone_autoship_bot`), not the existing `@blackstone` bot used by the live notification stack.

## Why

Three forces drove this:

1. **Agno's Telegram interface is webhook-only and message-only.** `agno/os/interfaces/telegram/router.py:135` reads `body.get("message")` and `body.get("edited_message")` and ignores everything else. It does **not** handle `callback_query` events.

2. **Our existing telegram-poller (`edgexpert/telegram-poller/poller.py`, 1067 LOC) is callback-query-driven.** Its primary job is dispatching from inline-keyboard buttons on Claude Queue confirmations, matter-assign callbacks, and triage callbacks. Tearing it down to put Agno's webhook on the same bot would require reimplementing all that logic on the Agno side.

3. **Telegram only allows ONE delivery mode per bot.** Polling and webhook are mutually exclusive — you can't run the existing poller and Agno's webhook against the same bot token simultaneously.

A two-bot split sidesteps all three: the existing bot keeps polling for callback_query confirmations (zero risk to live infra), and the new bot serves the auto-ship chat / `/halt-auto-ship` / `/resume-auto-ship` commands via Agno's native webhook.

## Consequences

**Positive:**
- Zero risk to the live notification + dispatch system (poller untouched).
- ~5 min setup vs. 6–12 hr of patching Agno's router to handle callback_query and migrating poller logic.
- Clean separation: `@blackstone` = "system events I need to confirm", `@blackstone_autoship_bot` = "I want to talk to / control the auto-ship platform".

**Negative:**
- Two bot icons in Ryan's Telegram client. Cosmetic.
- If we ever want auto-ship to push button-based confirmations (it won't initially), we'd need to revisit.

**Neutral:**
- Bot token storage: new token goes alongside existing one in `~/.blackstone-secrets/` (e.g. `telegram-autoship-bot-token.txt`) on EdgeXpert, and as a Railway env var (`TELEGRAM_TOKEN`) at deploy time.
- Webhook URL: when deployed to Railway, the public HTTPS URL serves Agno's `/telegram/webhook`. For EdgeXpert dev, use ngrok or skip Telegram in dev mode.

## Implementation pointers (for the Step 5 implementer)

1. Create bot via @BotFather → save token to `~/.blackstone-secrets/telegram-autoship-bot-token.txt` on EdgeXpert.
2. Add `pyTelegramBotAPI`, `aiohttp`, `anthropic` to `requirements.txt`.
3. In `app/main.py`, conditionally instantiate `Telegram(workflow=auto_ship_workflow, token=...)` when `TELEGRAM_TOKEN` env is present.
4. For `/halt-auto-ship` / `/resume-auto-ship`: subclass `Telegram` and intercept commands before super's `_handle_command` (router.py:148–167); back the halt state with a row in agentos-db.
5. After deploy: register webhook via `setWebhook` API call against the deployed `/telegram/webhook` URL.

## Alternatives considered

- **Patch Agno to handle callback_query + migrate poller (one bot, full UX):** Rejected. ~6–12 hr of work, requires tearing down a known-working production poller, and lock-step migration risk.
- **Defer Telegram interface entirely for v1:** Rejected. The halt/resume control surface is a meaningful safety lever for an auto-ship platform; control via raw HTTP is a regression vs. a Slack/Telegram bot.
