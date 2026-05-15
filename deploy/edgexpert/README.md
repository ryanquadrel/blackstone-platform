# EdgeXpert deployment

Deploys AgentOS as a systemd-managed Docker Compose stack on the firm's
EdgeXpert appliance (ARM64 Ubuntu) or any Linux host with `docker` + `systemd`.

This replaces the upstream's Railway deploy. See
[`docs/decisions/0001-telegram-two-bots.md`](../../docs/decisions/0001-telegram-two-bots.md)
for the matching Telegram architecture decision.

## Files

| File | Purpose |
|---|---|
| `compose.prod.yaml` | Production Docker Compose stack (`restart: always`, no `--reload`, healthchecks, Postgres bound to loopback). |
| `agentos.service` | systemd unit that runs `docker compose up -d --build` on boot, `docker compose down` on shutdown. |
| `install.sh` | One-shot installer — symlinks the repo into `/opt/blackstone-platform`, copies the unit, enables + starts it. |

## First-time install

```bash
# 1. Clone into any location you like
git clone https://github.com/ryanquadrel/blackstone-platform.git ~/blackstone-platform
cd ~/blackstone-platform

# 2. Populate .env (uses platform-wide defaults for vLLM at spark-1)
cp example.env .env
# Edit .env — set any non-defaults (AGENTOS_PORT if 8000 is taken, TELEGRAM_TOKEN, etc.)

# 3. Install + start
sudo bash deploy/edgexpert/install.sh
```

The installer:

- Symlinks `/opt/blackstone-platform → <your clone>` so the unit path is stable.
- Copies `agentos.service` to `/etc/systemd/system/`.
- Enables it for boot, restarts to apply.

## Updating

```bash
cd /opt/blackstone-platform   # follows the symlink
git pull
sudo systemctl restart agentos.service
```

The unit type is `oneshot` + `RemainAfterExit`, so `restart` re-runs
`docker compose up -d --build` which picks up code changes.

## Logs

```bash
# systemd-level (start/stop events)
journalctl -u agentos.service -f

# Container-level (the actual app)
docker compose -f /opt/blackstone-platform/deploy/edgexpert/compose.prod.yaml logs -f agentos-api
```

## Healthchecks

Both containers ship Docker healthchecks. `docker compose ps` shows
`healthy` once the api container can answer `GET /agents` and Postgres
answers `pg_isready`. The api container waits for the db's health, not
just its existence — first-boot ordering is handled.

## Port conflicts

EdgeXpert hosts often run other services on common ports. Override host-side
mappings via env vars (defaults preserve upstream):

| Env var | Default | Note |
|---|---|---|
| `AGENTOS_PORT` | `8000` | On EdgeXpert, vLLM owns 8000 — set this to `8765` or similar. |
| `DB_PORT_HOST` | `5432` | Bound to `127.0.0.1` only in prod (not LAN-exposed). |

## Differences from `compose.yaml` (dev)

| Aspect | Dev (`compose.yaml`) | Prod (`compose.prod.yaml`) |
|---|---|---|
| `restart` | `unless-stopped` | `always` |
| `--reload` | yes (hot-reload) | no (production uvicorn) |
| Bind mount `.:/app` | yes (live code edit) | no (image is source of truth) |
| `RUNTIME_ENV` | `dev` (JWT off) | `prd` (JWT gate active — needs `JWT_VERIFICATION_KEY`) |
| `AGNO_DEBUG` | `True` | `False` |
| Postgres bind | `0.0.0.0:5432` | `127.0.0.1:5432` |
| Healthchecks | none | both services |
