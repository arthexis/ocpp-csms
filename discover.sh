#!/bin/sh
set -eu

if [ "$(id -u)" -eq 0 ]; then
    printf 'Do not run discover.sh as root or with sudo. Run it as the normal appliance user.\n' >&2
    exit 1
fi

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PREFIX=${OCPP_CSMS_PREFIX:-"$HOME/.local/share/ocpp-csms"}
DATA_DIR=${OCPP_CSMS_DATA_DIR:-"$HOME/ocpp-csms-data"}
PORT=${OCPP_CSMS_PORT:-9000}
INTERFACE=${OCPP_DISCOVER_INTERFACE:-eth0}
VENV="$PREFIX/venv"
PYTHON="$VENV/bin/python"
DISCOVER_ROOT="$PREFIX/discover"
SERVICE_NAME=ocpp-discover.service
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"
SERVICE_TEMPLATE="$ROOT/systemd/ocpp-discover.service.in"
MODE=run

usage() {
    cat <<'EOF'
Usage: sh discover.sh [--interface IFACE] [--install|--uninstall|--cleanup]

Run OCPP network discovery immediately by default.

Options:
  --interface IFACE  Charger-facing Ethernet interface (default: eth0).
  --install          Install dependencies and enable OCPP Discover at boot.
  --uninstall        Disable/remove OCPP Discover and clean discovery-owned state.
  --cleanup          Remove discovery-owned network state without uninstalling.
  -h, --help         Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --interface)
            [ "$#" -ge 2 ] || { printf 'Missing value for --interface\n' >&2; exit 2; }
            INTERFACE=$2
            shift 2
            ;;
        --interface=*)
            INTERFACE=${1#*=}
            shift
            ;;
        --install|--uninstall|--cleanup)
            [ "$MODE" = run ] || { printf 'Choose only one discovery mode.\n' >&2; exit 2; }
            MODE=${1#--}
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

need() {
    command -v "$1" >/dev/null 2>&1
}

require_base_install() {
    [ -x "$PYTHON" ] || {
        printf 'OCPP CSMS is not installed at %s. Run sh install.sh first.\n' "$PREFIX" >&2
        exit 1
    }
}

missing_runtime() {
    missing=""
    for command in tcpdump nft ip; do
        if ! need "$command"; then
            missing="$missing $command"
        fi
    done
    printf '%s' "$missing"
}

show_dependency_help() {
    printf 'OCPP Discover requires: tcpdump nft ip\n' >&2
    printf 'Install the Debian packages with:\n\n' >&2
    printf '  sudo apt-get update\n' >&2
    printf '  sudo apt-get install -y tcpdump nftables iproute2\n' >&2
}

install_dependencies() {
    missing=$(missing_runtime)
    [ -n "$missing" ] || return 0

    if [ ! -r /etc/os-release ]; then
        show_dependency_help
        exit 1
    fi
    # shellcheck disable=SC1091
    . /etc/os-release
    if [ "${ID:-}" != debian ]; then
        printf 'Automatic dependency installation is supported only on Debian.\n' >&2
        show_dependency_help
        exit 1
    fi
    sudo apt-get update
    sudo apt-get install -y tcpdump nftables iproute2
}

escape_sed() {
    printf '%s' "$1" | sed 's/[\\&|]/\\&/g'
}

install_discovery() {
    require_base_install
    need sudo || { printf 'Missing required command: sudo\n' >&2; exit 1; }
    need systemctl || { printf 'Missing required command: systemctl\n' >&2; exit 1; }
    install_dependencies

    mkdir -p "$DISCOVER_ROOT/field"
    cp "$ROOT/field/__init__.py" "$DISCOVER_ROOT/field/__init__.py"
    cp "$ROOT/field/discover.py" "$DISCOVER_ROOT/field/discover.py"
    cp "$ROOT/field/redirect.py" "$DISCOVER_ROOT/field/redirect.py"

    PYTHON_ESC=$(escape_sed "$PYTHON")
    DISCOVER_ESC=$(escape_sed "$DISCOVER_ROOT")
    DATA_ESC=$(escape_sed "$DATA_DIR")
    INTERFACE_ESC=$(escape_sed "$INTERFACE")
    TMP_SERVICE=$(mktemp)
    trap 'rm -f "$TMP_SERVICE"' EXIT HUP INT TERM
    sed \
        -e "s|@PYTHON@|$PYTHON_ESC|g" \
        -e "s|@DISCOVER_ROOT@|$DISCOVER_ESC|g" \
        -e "s|@DATA_DIR@|$DATA_ESC|g" \
        -e "s|@INTERFACE@|$INTERFACE_ESC|g" \
        -e "s|@PORT@|$PORT|g" \
        "$SERVICE_TEMPLATE" > "$TMP_SERVICE"

    if need systemd-analyze; then
        systemd-analyze verify "$TMP_SERVICE" >/dev/null
    fi
    sudo install -m 0644 "$TMP_SERVICE" "$SERVICE_PATH"
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"
    sudo systemctl start --no-block "$SERVICE_NAME"
    printf 'Installed OCPP Discover on %s; service enabled for boot.\n' "$INTERFACE"
}

cleanup_discovery() {
    require_base_install
    runtime_root=$ROOT
    if [ -f "$DISCOVER_ROOT/field/discover.py" ]; then
        runtime_root=$DISCOVER_ROOT
    fi
    (
        cd "$runtime_root"
        sudo "$PYTHON" -m field.discover cleanup --state-dir /run/ocpp-discover
    )
}

uninstall_discovery() {
    require_base_install
    need sudo || { printf 'Missing required command: sudo\n' >&2; exit 1; }
    need systemctl || { printf 'Missing required command: systemctl\n' >&2; exit 1; }
    sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    cleanup_discovery || true
    sudo systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    sudo rm -f "$SERVICE_PATH"
    sudo systemctl daemon-reload
    rm -rf "$DISCOVER_ROOT"
    printf 'Uninstalled OCPP Discover. OCPP CSMS remains installed.\n'
}

run_discovery() {
    require_base_install
    missing=$(missing_runtime)
    if [ -n "$missing" ]; then
        show_dependency_help
        exit 1
    fi
    (
        cd "$ROOT"
        sudo "$PYTHON" -m field.discover run \
            --data-dir "$DATA_DIR" \
            --state-dir /run/ocpp-discover \
            --interface "$INTERFACE" \
            --listen-port "$PORT"
    )
}

case "$MODE" in
    run) run_discovery ;;
    install) install_discovery ;;
    uninstall) uninstall_discovery ;;
    cleanup) cleanup_discovery ;;
esac
