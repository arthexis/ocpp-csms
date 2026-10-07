#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ -n "${OCPP_CSMS_VENV:-}" ]; then
    COMMAND="$OCPP_CSMS_VENV/bin/ocpp-csms"
elif [ -x "$ROOT/.venv/bin/ocpp-csms" ]; then
    COMMAND="$ROOT/.venv/bin/ocpp-csms"
else
    PREFIX=${OCPP_CSMS_PREFIX:-"$HOME/.local/share/ocpp-csms"}
    COMMAND="$PREFIX/current/venv/bin/ocpp-csms"
fi

if [ ! -x "$COMMAND" ]; then
    printf 'CSMS virtual environment not found.\n' >&2
    printf 'Run ./deploy.sh for appliance deployment, or create .venv and install the project for development.\n' >&2
    exit 1
fi

exec "$COMMAND" "$@"
