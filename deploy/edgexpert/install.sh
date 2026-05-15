#!/usr/bin/env bash
# Install the auto-ship platform as a systemd-managed Docker Compose stack
# on EdgeXpert (or any Linux host with docker + systemd).
#
# Run from a clone of this repo, e.g. /opt/blackstone-platform:
#   sudo bash deploy/edgexpert/install.sh
#
# This is idempotent — safe to re-run after a code change to rebuild +
# restart. It does NOT manage the .env file; populate that first.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
CANONICAL_DIR="/opt/blackstone-platform"
UNIT_NAME="agentos.service"

if [[ "$EUID" -ne 0 ]]; then
    echo "Run as root: sudo bash $0" >&2
    exit 1
fi

if [[ ! -f "${REPO_DIR}/.env" ]]; then
    echo "Missing ${REPO_DIR}/.env — copy example.env to .env and configure it first." >&2
    exit 1
fi

# Symlink the repo into the canonical path the systemd unit expects.
# Skip if the repo IS the canonical path (idempotent re-install).
if [[ "$REPO_DIR" != "$CANONICAL_DIR" ]]; then
    if [[ -L "$CANONICAL_DIR" ]]; then
        rm "$CANONICAL_DIR"
    elif [[ -e "$CANONICAL_DIR" ]]; then
        echo "$CANONICAL_DIR exists and is not a symlink. Move or remove it before installing." >&2
        exit 1
    fi
    ln -s "$REPO_DIR" "$CANONICAL_DIR"
    echo "Symlinked $CANONICAL_DIR -> $REPO_DIR"
fi

cp "${REPO_DIR}/deploy/edgexpert/${UNIT_NAME}" "/etc/systemd/system/${UNIT_NAME}"
systemctl daemon-reload
systemctl enable "${UNIT_NAME}"
systemctl restart "${UNIT_NAME}"

echo ""
echo "Installed. Useful commands:"
echo "  systemctl status ${UNIT_NAME}"
echo "  journalctl -u ${UNIT_NAME} -f"
echo "  docker compose -f ${REPO_DIR}/deploy/edgexpert/compose.prod.yaml logs -f agentos-api"
