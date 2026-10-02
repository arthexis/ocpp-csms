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
VENV="$PREFIX/venv"
COMMAND="$BIN_DIR/ocpp-csms"
DATABASE_NAME=ocpp-csms.sqlite3
SERVICE_NAME=ocpp-csms.service
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"
SERVICE_TEMPLATE="$ROOT/systemd/ocpp-csms.service.in"
INSTALL_USER=$(id -un)
INSTALL_GROUP=$(id -gn)

need() {
    command -v "$1" >/dev/null 2>&1 || {
        printf 'Missing required command: %s\n' "$1" >&2
        exit 1
    }
}

need python3
need systemctl
need sudo

python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install "$ROOT"

mkdir -p "$BIN_DIR" "$DATA_DIR"
ln -sf "$VENV/bin/ocpp-csms" "$COMMAND"

[ -x "$COMMAND" ] || {
    printf 'Installed command is not executable: %s\n' "$COMMAND" >&2
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

TMP_SERVICE=$(mktemp)
trap 'rm -f "$TMP_SERVICE"' EXIT HUP INT TERM
sed \
    -e "s|@USER@|$USER_ESC|g" \
    -e "s|@GROUP@|$GROUP_ESC|g" \
    -e "s|@HOME@|$HOME_ESC|g" \
    -e "s|@COMMAND@|$COMMAND_ESC|g" \
    -e "s|@DATA_DIR@|$DATA_ESC|g" \
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

# Verify the process actually accepts TCP connections on the default OCPP port.
if ! "$VENV/bin/python" - <<'PY'
import socket
import time

for _ in range(20):
    try:
        with socket.create_connection(("127.0.0.1", 9000), timeout=0.5):
            raise SystemExit(0)
    except OSError:
        time.sleep(0.25)
raise SystemExit(1)
PY
then
    printf 'Service is active but port 9000 is not accepting connections. Recent logs:\n' >&2
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
printf 'Command: %s\n' "$COMMAND"
printf 'Data:    %s\n' "$DATA_DIR"
printf 'Service: %s (active, enabled, listening on port 9000)\n' "$SERVICE_NAME"
printf '\nUseful commands:\n'
printf '  %s status\n' "$COMMAND"
printf '  sudo systemctl status %s\n' "$SERVICE_NAME"
printf '  sudo journalctl -u %s -f\n' "$SERVICE_NAME"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) printf 'Add %s to PATH to run ocpp-csms directly.\n' "$BIN_DIR" ;;
esac
