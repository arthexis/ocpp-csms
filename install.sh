#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PREFIX=${OCPP_CSMS_PREFIX:-"$HOME/.local/share/ocpp-csms"}
BIN_DIR=${OCPP_CSMS_BIN_DIR:-"$HOME/.local/bin"}
DATA_DIR=${OCPP_CSMS_DATA_DIR:-"$HOME/ocpp-csms-data"}
HOST=${OCPP_CSMS_HOST:-"0.0.0.0"}
PORT=${OCPP_CSMS_PORT:-9000}
VENV="$PREFIX/venv"
STAGE_VENV="$PREFIX/venv.next"
PREVIOUS_VENV="$PREFIX/venv.previous"
COMMAND="$BIN_DIR/ocpp-csms"
CSMS_COMMAND="$BIN_DIR/csms"
DATABASE_NAME=ocpp-csms.sqlite3
SERVICE_NAME=ocpp-csms.service
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"
SERVICE_TEMPLATE="$ROOT/systemd/ocpp-csms.service.in"
INSTALL_USER=$(id -un)
INSTALL_GROUP=$(id -gn)
DISCOVER_MODE=preserve
PROMOTED=0

usage() {
    cat <<'EOF'
Usage: sh install.sh [--host HOST] [--port PORT] [--with-discover|--without-discover]

Install or update the OCPP CSMS appliance service using a safe staged handoff.
Idle connected chargers are expected and are handed to the replacement service.
Active charging always blocks installation.

Options:
  --host HOST          Listener address for the CSMS (default: 0.0.0.0).
  --port PORT          Listener port for the CSMS (default: 9000).
  --with-discover      Also install and enable OCPP Discover. Discover can learn the
                       charger-facing Ethernet address/port at boot and adapt the
                       local network so plaintext OCPP reaches this CSMS.
  --without-discover   Disable/uninstall OCPP Discover and clean its owned network state.
  -h, --help           Show this help.

Without either discovery flag, the existing OCPP Discover enabled/disabled state is preserved.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --host) [ "$#" -ge 2 ] || { printf 'Missing value for --host\n' >&2; exit 2; }; HOST=$2; shift 2 ;;
        --host=*) HOST=${1#*=}; shift ;;
        --port) [ "$#" -ge 2 ] || { printf 'Missing value for --port\n' >&2; exit 2; }; PORT=$2; shift 2 ;;
        --port=*) PORT=${1#*=}; shift ;;
        --with-discover)
            [ "$DISCOVER_MODE" = preserve ] || { printf 'Choose only one discovery install option.\n' >&2; exit 2; }
            DISCOVER_MODE=install; shift ;;
        --without-discover)
            [ "$DISCOVER_MODE" = preserve ] || { printf 'Choose only one discovery install option.\n' >&2; exit 2; }
            DISCOVER_MODE=uninstall; shift ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -eq 0 ]; then
    printf 'Do not run this installer as root or with sudo. Run: sh install.sh\n' >&2
    exit 1
fi
[ -n "$HOST" ] || { printf 'Listener host must not be empty.\n' >&2; exit 2; }
case "$PORT" in ''|*[!0-9]*) printf 'Listener port must be an integer: %s\n' "$PORT" >&2; exit 2 ;; esac
if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
    printf 'Listener port must be between 1 and 65535: %s\n' "$PORT" >&2
    exit 2
fi

need() {
    command -v "$1" >/dev/null 2>&1 || { printf 'Missing required command: %s\n' "$1" >&2; exit 1; }
}

run_preflight() {
    PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m ocpp_csms.install_preflight --data-dir "$DATA_DIR" "$@"
}

ensure_user_bin_on_path() {
    case ":$PATH:" in *":$BIN_DIR:"*) return 0 ;; esac
    shell_name=$(basename "${SHELL:-sh}")
    case "$shell_name" in
        bash) rc="$HOME/.bashrc"; path_line="export PATH=\"$BIN_DIR:\$PATH\"" ;;
        zsh) rc="$HOME/.zshrc"; path_line="export PATH=\"$BIN_DIR:\$PATH\"" ;;
        fish) rc="$HOME/.config/fish/config.fish"; path_line="fish_add_path \"$BIN_DIR\"" ;;
        *) rc="$HOME/.profile"; path_line="export PATH=\"$BIN_DIR:\$PATH\"" ;;
    esac
    mkdir -p "$(dirname "$rc")"
    if [ ! -f "$rc" ] || ! grep -F "$path_line" "$rc" >/dev/null 2>&1; then
        printf '\n# OCPP CSMS user commands\n%s\n' "$path_line" >> "$rc"
    fi
}

escape_sed() { printf '%s' "$1" | sed 's/[\\&|]/\\&/g'; }

render_service() {
    service_command=$1
    command_esc=$(escape_sed "$service_command")
    sed \
        -e "s|@USER@|$USER_ESC|g" \
        -e "s|@GROUP@|$GROUP_ESC|g" \
        -e "s|@HOME@|$HOME_ESC|g" \
        -e "s|@COMMAND@|$command_esc|g" \
        -e "s|@DATA_DIR@|$DATA_ESC|g" \
        -e "s|@HOST@|$HOST_ESC|g" \
        -e "s|@PORT@|$PORT|g" \
        "$SERVICE_TEMPLATE" > "$TMP_SERVICE"
}

need python3
# First safety gate: no appliance mutation has happened yet.
run_preflight

need systemctl
need sudo

TMP_FINAL=$(mktemp)
TMP_BASELINE=$(mktemp)
TMP_SERVICE=$(mktemp --suffix=.service)
TMP_OLD_SERVICE=$(mktemp)
cleanup() {
    rm -f "$TMP_FINAL" "$TMP_BASELINE" "$TMP_SERVICE" "$TMP_OLD_SERVICE"
    if [ "$PROMOTED" -eq 0 ]; then rm -rf "$STAGE_VENV"; fi
}
trap cleanup EXIT HUP INT TERM

# Expensive/fallible work is staged while the old CSMS remains untouched.
mkdir -p "$PREFIX"
rm -rf "$STAGE_VENV"
python3 -m venv "$STAGE_VENV"
"$STAGE_VENV/bin/python" -m pip install --upgrade pip
"$STAGE_VENV/bin/python" -m pip install "$ROOT"
"$STAGE_VENV/bin/ocpp-csms" --help >/dev/null
SCHEMA_ACTION=$("$STAGE_VENV/bin/python" -m ocpp_csms.install_cutover schema-check --data-dir "$DATA_DIR")

USER_ESC=$(escape_sed "$INSTALL_USER")
GROUP_ESC=$(escape_sed "$INSTALL_GROUP")
HOME_ESC=$(escape_sed "$HOME")
DATA_ESC=$(escape_sed "$DATA_DIR")
HOST_ESC=$(escape_sed "$HOST")

# Verify the candidate using the staged executable that already exists. The
# public command link is intentionally created only after successful promotion.
render_service "$STAGE_VENV/bin/ocpp-csms"
if command -v systemd-analyze >/dev/null 2>&1; then systemd-analyze verify "$TMP_SERVICE" >/dev/null; fi

# Second safety gate: a charge may have started while staging.
run_preflight --json > "$TMP_FINAL"
"$STAGE_VENV/bin/python" -m ocpp_csms.install_cutover capture-baseline \
    --data-dir "$DATA_DIR" --preflight-json "$TMP_FINAL" --output "$TMP_BASELINE"

HAD_RUNNING_SERVICE=0
HAD_SERVICE_FILE=0
HAD_VENV=0
if sudo systemctl is-active --quiet "$SERVICE_NAME"; then HAD_RUNNING_SERVICE=1; fi
if sudo test -f "$SERVICE_PATH"; then
    sudo cat "$SERVICE_PATH" > "$TMP_OLD_SERVICE"
    HAD_SERVICE_FILE=1
fi
if [ -d "$VENV" ]; then HAD_VENV=1; fi

if [ "$HAD_RUNNING_SERVICE" -eq 1 ]; then sudo systemctl stop "$SERVICE_NAME"; fi

# The old process no longer owns the database. Only now may its schema change.
if [ "$SCHEMA_ACTION" = upgrade ]; then
    "$STAGE_VENV/bin/python" -m ocpp_csms.install_cutover schema-upgrade --data-dir "$DATA_DIR" >/dev/null
fi

rm -rf "$PREVIOUS_VENV"
if [ "$HAD_VENV" -eq 1 ]; then mv "$VENV" "$PREVIOUS_VENV"; fi
mv "$STAGE_VENV" "$VENV"
PROMOTED=1

# Console scripts generated while the environment was named venv.next carry
# that absolute interpreter path. Reinstall from the local checkout after the
# promotion so the commands use the final venv path before systemd starts it.
"$VENV/bin/python" -m pip install --no-deps --force-reinstall "$ROOT"

mkdir -p "$BIN_DIR" "$DATA_DIR"
ln -sf "$VENV/bin/ocpp-csms" "$COMMAND"
ln -sf "$VENV/bin/ocpp-csms" "$CSMS_COMMAND"
ensure_user_bin_on_path

# The live unit must point at the promoted environment, not the retired staging
# path used for pre-cutover validation.
render_service "$VENV/bin/ocpp-csms"

rollback_startup() {
    printf 'Replacement CSMS failed during handoff.\n' >&2
    if [ "$SCHEMA_ACTION" != upgrade ] && [ "$HAD_VENV" -eq 1 ] && [ -d "$PREVIOUS_VENV" ]; then
        sudo systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
        rm -rf "$VENV.failed"
        mv "$VENV" "$VENV.failed"
        mv "$PREVIOUS_VENV" "$VENV"
        ln -sf "$VENV/bin/ocpp-csms" "$COMMAND"
        ln -sf "$VENV/bin/ocpp-csms" "$CSMS_COMMAND"
        if [ "$HAD_SERVICE_FILE" -eq 1 ]; then sudo install -m 0644 "$TMP_OLD_SERVICE" "$SERVICE_PATH"; fi
        sudo systemctl daemon-reload
        if [ "$HAD_RUNNING_SERVICE" -eq 1 ]; then sudo systemctl start "$SERVICE_NAME" || true; fi
        printf 'Previous CSMS environment restored. Failed replacement kept at %s.failed\n' "$VENV" >&2
    elif [ "$SCHEMA_ACTION" = upgrade ]; then
        printf 'Schema was upgraded; automatic rollback is intentionally disabled. Previous environment and schema backup were preserved.\n' >&2
    fi
    exit 1
}

# init creates a brand-new current schema when needed and is a no-op for a current/upgraded DB.
if ! "$COMMAND" --data-dir "$DATA_DIR" init; then rollback_startup; fi
[ -f "$DATA_DIR/$DATABASE_NAME" ] || rollback_startup
[ -d "$DATA_DIR/transactions" ] || rollback_startup

sudo install -m 0644 "$TMP_SERVICE" "$SERVICE_PATH"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME" >/dev/null
if ! sudo systemctl restart "$SERVICE_NAME"; then rollback_startup; fi
if ! sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    sudo journalctl -u "$SERVICE_NAME" -n 20 --no-pager >&2 || true
    rollback_startup
fi

if ! "$VENV/bin/python" - "$HOST" "$PORT" <<'PY'
import socket
import sys
import time
host = sys.argv[1]
port = int(sys.argv[2])
probe_host = "127.0.0.1" if host == "0.0.0.0" else "::1" if host == "::" else host
for _ in range(20):
    try:
        with socket.create_connection((probe_host, port), timeout=0.5):
            raise SystemExit(0)
    except OSError:
        time.sleep(0.25)
raise SystemExit(1)
PY
then
    sudo journalctl -u "$SERVICE_NAME" -n 20 --no-pager >&2 || true
    rollback_startup
fi

# Every charger connected at the final gate must emit a fresh connection event.
if ! "$VENV/bin/python" -m ocpp_csms.install_cutover wait-reconnect \
    --data-dir "$DATA_DIR" --baseline "$TMP_BASELINE" --timeout 30; then
    printf 'Service handoff incomplete: one or more previously connected chargers did not reconnect. The replacement CSMS remains running for diagnosis.\n' >&2
    exit 1
fi

[ -w "$DATA_DIR" ] || { printf 'Data directory stopped being writable by %s: %s\n' "$INSTALL_USER" "$DATA_DIR" >&2; exit 1; }
"$COMMAND" --data-dir "$DATA_DIR" status >/dev/null

case "$DISCOVER_MODE" in
    install|uninstall)
        # Discover is a later network mutation: active charging still blocks it.
        PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m ocpp_csms.install_preflight --data-dir "$DATA_DIR" >/dev/null
        if [ "$DISCOVER_MODE" = install ]; then
            OCPP_CSMS_PREFIX="$PREFIX" OCPP_CSMS_DATA_DIR="$DATA_DIR" OCPP_CSMS_PORT="$PORT" sh "$ROOT/discover.sh" --install
        else
            OCPP_CSMS_PREFIX="$PREFIX" OCPP_CSMS_DATA_DIR="$DATA_DIR" OCPP_CSMS_PORT="$PORT" sh "$ROOT/discover.sh" --uninstall
        fi
        ;;
    preserve) ;;
esac

printf 'Installed OCPP CSMS appliance for %s\n' "$INSTALL_USER"
printf 'Command: %s\n' "$CSMS_COMMAND"
printf 'Data:    %s\n' "$DATA_DIR"
printf 'Service: %s (active, enabled, listening on %s:%s)\n' "$SERVICE_NAME" "$HOST" "$PORT"
case "$SCHEMA_ACTION" in upgrade) printf 'Schema: upgraded with backup preserved\n' ;; *) printf 'Schema: current\n' ;; esac
case "$DISCOVER_MODE" in
    install) printf 'Discover: ocpp-discover.service enabled\n' ;;
    uninstall) printf 'Discover: disabled/uninstalled\n' ;;
    preserve) printf 'Discover: existing state preserved\n' ;;
esac
printf '\nUseful commands:\n  csms status\n  sudo systemctl status %s\n  sudo journalctl -u %s -f\n' "$SERVICE_NAME" "$SERVICE_NAME"
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) printf 'Open a new shell, or source your shell configuration, before running csms directly.\n' ;; esac
