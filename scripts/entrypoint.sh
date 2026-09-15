#!/bin/bash

############################################################################
#
#    Agno Container Entrypoint
#
############################################################################

# Colors
ORANGE='\033[38;5;208m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

echo ""
echo -e "${ORANGE}"
cat << 'BANNER'
     █████╗  ██████╗ ███╗   ██╗ ██████╗
    ██╔══██╗██╔════╝ ████╗  ██║██╔═══██╗
    ███████║██║  ███╗██╔██╗ ██║██║   ██║
    ██╔══██║██║   ██║██║╚██╗██║██║   ██║
    ██║  ██║╚██████╔╝██║ ╚████║╚██████╔╝
    ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝ ╚═════╝
BANNER
echo -e "${NC}"

if [[ "$WAIT_FOR_DB" = true || "$WAIT_FOR_DB" = True ]]; then
    # Pure-bash TCP probe. The dockerize binary shipped in agnohq/python is
    # x86_64-only and exits "Exec format error" on arm64 hosts, which used to
    # fall through to "Database ready." without ever waiting.
    DB_WAIT_TIMEOUT="${DB_WAIT_TIMEOUT:-300}"
    if [[ -z "$DB_HOST" || -z "$DB_PORT" ]]; then
        echo -e "    ${BOLD}WAIT_FOR_DB is set but DB_HOST or DB_PORT is empty.${NC}" >&2
        exit 1
    fi
    echo -e "    ${DIM}Waiting for database at ${DB_HOST}:${DB_PORT} (timeout ${DB_WAIT_TIMEOUT}s)...${NC}"
    deadline=$((SECONDS + DB_WAIT_TIMEOUT))
    until timeout 2 bash -c "exec 3<>/dev/tcp/${DB_HOST}/${DB_PORT}" 2>/dev/null; do
        if ((SECONDS >= deadline)); then
            echo -e "    ${BOLD}Database not reachable at ${DB_HOST}:${DB_PORT} after ${DB_WAIT_TIMEOUT}s.${NC}" >&2
            exit 1
        fi
        sleep 1
    done
    echo -e "    ${BOLD}Database ready.${NC}"
    echo ""
fi

case "$1" in
    chill)
        echo -e "    ${DIM}Mode: chill${NC}"
        echo -e "    ${BOLD}Container running.${NC}"
        echo ""
        while true; do sleep 18000; done
        ;;
    *)
        echo -e "    ${DIM}> $@${NC}"
        echo ""
        exec "$@"
        ;;
esac
