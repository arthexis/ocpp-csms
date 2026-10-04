#!/bin/sh
set -eu

if [ "$(id -u)" -eq 0 ]; then
    printf 'Do not run this installer as root or with sudo. Run: sh install.sh\n' >&2
    exit 1
fi

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PREFIX=${OCPP_CSMS_PREFIX:-"$HOME/.local/share/ocpp-csms"}
BIN_DIR=${OCPP_CSMS_BIN_DIR:-"$HOME/.local/bin"}
DATA_DIR=${OCPP_CSMS_DATA_DIR:-"$HOME/ocpp-csms-data"}
HOST=${OCPP_CSMS_HOST:-"0.0.0.0"}
PORT=${OCPP_CSMS_PORT:-9000}
VENV="$PREFIX/venv"
COMMAND="$BIN_DIR/ocpp-csms"
CSMS_COMMAND="$BIN_DIR/csms"
DATABASE_NAME=ocpp-csms.sqlite3
SERVICE_NAME=ocpp-csms.service
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"
SERVICE_TEMPLATE="$ROOT/systemd/ocpp-csms.service.in"
INSTALL_USER=$(id -un)
INSTALL_GROUP=$(id -gn)

usage() {
    printf 'Usage: sh install.sh [--host HOST] [--port PORT]\n'
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --host)
            [ "$#" -ge 2 ] || { printf 'Missing value for --host\n' >&2; exit 2; }
            HOST=$2
            shift 2
            ;;
        --host=*)
            HOST=${1#*=}
            shift
            ;;
        --port)
            [ "$#" -ge 2 ] || { printf 'Missing value for --port\n' >&2; exit 2; }
            PORT=$2
            shift 2
            ;;
        --port=*)
            PORT=${1#*=}
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown option: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

[ -n "$HOST" ] || {
    printf 'Listener host must not be empty.\n' >&2
    exit 2
}
case "$PORT" in
    ''|*[!0-9]*)
        printf 'Listener port must be an integer: %s\n' "$PORT" >&2
        exit 2
        ;;
esac
if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
    printf 'Listener port must be between 1 and 65535: %s\n' "$PORT" >&2
    exit 2
fi

need() {
    command -v "$1" >/dev/null 2>&1 || {
        printf 'Missing required command: %s\n' "$1" >&2
        exit 1
    }
}

ensure_user_bin_on_path() {
    case ":$PATH:" in
        *":$BIN_DIR:"*) return 0 ;;
    esac

    shell_name=$(basename "${SHELL:-sh}")
    case "$shell_name" in
        bash) rc="$HOME/.bashrc"; path_line='export PATH="$HOME/.local/bin:$PATH"' ;;
        zsh) rc="$HOME/.zshrc"; path_line='export PATH="$HOME/.local/bin:$PATH"' ;;
        fish) rc="$HOME/.config/fish/config.fish"; path_line='fish_add_path "$HOME/.local/bin"' ;;
        *) rc="$HOME/.profile"; path_line='export PATH="$HOME/.local/bin:$PATH"' ;;
    esac

    mkdir -p "$(dirname "$rc")"
    if [ ! -f "$rc" ] || ! grep -F "$path_line" "$rc" >/dev/null 2>&1; then
        printf '\n# OCPP CSMS user commands\n%s\n' "$path_line" >> "$rc"
    fi
}

need python3
need systemctl
need sudo

python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install "$ROOT"

mkdir -p "$BIN_DIR" "$DATA_DIR"
ln -sf "$VENV/bin/ocpp-csms" "$COMMAND"
ln -sf "$VENV/bin/ocpp-csms" "$CSMS_COMMAND"
ensure_user_bin_on_path

[ -x "$COMMAND" ] || {
    printf 'Installed command is not executable: %s\n' "$COMMAND" >&2
    exit 1
}
[ -x "$CSMS_COMMAND" ] || {
    printf 'Installed command is not executable: %s\n' "$CSMS_COMMAND" >&2
    exit 1
}
[ -w "$DATA_DIR" ] || {
    printf 'Data directory is not writable: %s\n' "$DATA_DIR" >&2
    exit 1
}

# Initialize and validate the local evidence store before touching systemd.
"$COMMAND" --data-dir "$DATA_DIR" init
[ -f "$DATA_DIR/$DATABASE_NAME" ] || {
    printf 'SQLite evidence database was not created: %s/%s\n' "$DATA_DIR" "$DATABASE_NAME" >&2
    exit 1
}
[ -d "$DATA_DIR/transactions" ] || {
    printf 'Transaction archive was not created: %s/transactions\n' "$DATA_DIR" >&2
    exit 1
}

escape_sed() {
    printf '%s' "$1" | sed 's/[\\&|]/\\&/g'
}

USER_ESC=$(escape_sed "$INSTALL_USER")
GROUP_ESC=$(escape_sed "$INSTALL_GROUP")
HOME_ESC=$(escape_sed "$HOME")
COMMAND_ESC=$(escape_sed "$COMMAND")
DATA_ESC=$(escape_sed "$DATA_DIR")
HOST_ESC=$(escape_sed "$HOST")

TMP_SERVICE=$(mktemp)
trap 'rm -f "$TMP_SERVICE"' EXIT HUP INT TERM
sed \
    -e "s|@USER@|$USER_ESC|g" \
    -e "s|@GROUP@|$GROUP_ESC|g" \
    -e "s|@HOME@|$HOME_ESC|g" \
    -e "s|@COMMAND@|$COMMAND_ESC|g" \
    -e "s|@DATA_DIR@|$DATA_ESC|g" \
    -e "s|@HOST@|$HOST_ESC|g" \
    -e "s|@PORT@|$PORT|g" \
    "$SERVICE_TEMPLATE" > "$TMP_SERVICE"

if command -v systemd-analyze >/dev/null 2>&1; then
    systemd-analyze verify "$TMP_SERVICE" >/dev/null
fi

sudo install -m 0644 "$TMP_SERVICE" "$SERVICE_PATH"
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"

if ! sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    printf 'Service failed to start. Recent logs:\n' >&2
    sudo journalctl -u "$SERVICE_NAME" -n 20 --no-pager >&2 || true
    exit 1
fi

# Verify the process actually accepts TCP connections on the configured endpoint.
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
    printf 'Service is active but %s:%s is not accepting connections. Recent logs:\n' "$HOST" "$PORT" >&2
    sudo journalctl -u "$SERVICE_NAME" -n 20 --no-pager >&2 || true
    exit 1
fi

# The service must own no root-only state. Verify the data path is still usable
# by the installing user after systemd has started it.
[ -w "$DATA_DIR" ] || {
    printf 'Data directory stopped being writable by %s: %s\n' "$INSTALL_USER" "$DATA_DIR" >&2
    exit 1
}
"$COMMAND" --data-dir "$DATA_DIR" status >/dev/null

printf 'Installed OCPP CSMS appliance for %s\n' "$INSTALL_USER"
printf 'Command: %s\n' "$CSMS_COMMAND"
printf 'Data:    %s\n' "$DATA_DIR"
printf 'Service: %s (active, enabled, listening on %s:%s)\n' "$SERVICE_NAME" "$HOST" "$PORT"
printf '\nUseful commands:\n'
printf '  csms status\n'
printf '  sudo systemctl status %s\n' "$SERVICE_NAME"
printf '  sudo journalctl -u %s -f\n' "$SERVICE_NAME"

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) printf 'Open a new shell, or source your shell configuration, before running csms directly.\n' ;;
esac
