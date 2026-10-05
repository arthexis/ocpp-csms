#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PREFIX=${OCPP_CSMS_PREFIX:-"$HOME/.local/share/ocpp-csms"}
DATA_DIR=${OCPP_CSMS_DATA_DIR:-"$HOME/ocpp-csms-data"}
PORT=${OCPP_CSMS_PORT:-9000}
INTERFACE=${OCPP_DISCOVER_INTERFACE:-eth0}
GRACE_SECONDS=${OCPP_DISCOVER_GRACE_SECONDS:-10}
ARP_SECONDS=${OCPP_DISCOVER_ARP_SECONDS:-15}
TCP_SECONDS=${OCPP_DISCOVER_TCP_SECONDS:-15}
EXISTING_ENDPOINT_ONLY=0
DIAGNOSTIC_ONLY=0
PASSIVE_CAPTURE_LOG=
VENV="$PREFIX/venv"
PYTHON="$VENV/bin/python"
SERVICE_NAME=ocpp-discover.service
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"
SERVICE_TEMPLATE="$ROOT/systemd/ocpp-discover.service.in"
MODE=run

usage() {
    cat <<'EOF'
Usage: sh discover.sh [options] [--install|--uninstall|--cleanup]

Run OCPP network discovery immediately by default.

Options:
  --interface IFACE  Charger-facing Ethernet interface (default: eth0).
  --grace-seconds N  Wait for a live charger before capture (default: 10).
  --arp-seconds N    ARP capture duration if fallback is enabled (default: 15).
  --tcp-seconds N    Passive TCP capture duration (default: 15; diagnostic max: 300).
  --existing-endpoint-only  Do not fall back to ARP or claim an address.
  --diagnostic-only  Observe and report an existing endpoint without network mutation.
  --passive-capture-log PATH  Save the passive TCP transcript to a new local file.
  --install          Install dependencies and enable OCPP Discover at boot.
  --uninstall        Disable/remove OCPP Discover and all Discover-owned state.
  --cleanup          Remove current Discover-owned network state but keep durable adaptation.
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
        --interface=*) INTERFACE=${1#*=}; shift ;;
        --grace-seconds|--arp-seconds|--tcp-seconds|--passive-capture-log)
            [ "$#" -ge 2 ] || { printf 'Missing value for %s\n' "$1" >&2; exit 2; }
            case "$1" in
                --grace-seconds) GRACE_SECONDS=$2 ;;
                --arp-seconds) ARP_SECONDS=$2 ;;
                --tcp-seconds) TCP_SECONDS=$2 ;;
                --passive-capture-log) PASSIVE_CAPTURE_LOG=$2 ;;
            esac
            shift 2
            ;;
        --grace-seconds=*|--arp-seconds=*|--tcp-seconds=*|--passive-capture-log=*)
            value=${1#*=}
            case "$1" in
                --grace-seconds=*) GRACE_SECONDS=$value ;;
                --arp-seconds=*) ARP_SECONDS=$value ;;
                --tcp-seconds=*) TCP_SECONDS=$value ;;
                --passive-capture-log=*) PASSIVE_CAPTURE_LOG=$value ;;
            esac
            shift
            ;;
        --existing-endpoint-only) EXISTING_ENDPOINT_ONLY=1; shift ;;
        --diagnostic-only) DIAGNOSTIC_ONLY=1; EXISTING_ENDPOINT_ONLY=1; shift ;;
        --install|--uninstall|--cleanup)
            [ "$MODE" = run ] || { printf 'Choose only one discovery mode.\n' >&2; exit 2; }
            MODE=${1#--}
            shift
            ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -eq 0 ]; then
    printf 'Do not run discover.sh as root or with sudo. Run it as the normal appliance user.\n' >&2
    exit 1
fi

case "$PORT" in
    ''|*[!0-9]*) printf 'OCPP listener port must be an integer: %s\n' "$PORT" >&2; exit 2 ;;
esac
if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
    printf 'OCPP listener port must be between 1 and 65535: %s\n' "$PORT" >&2
    exit 2
fi

for value in "$GRACE_SECONDS" "$ARP_SECONDS" "$TCP_SECONDS"; do
    case "$value" in
        ''|*[!0-9]*) printf 'Discovery durations must be non-negative integer seconds: %s\n' "$value" >&2; exit 2 ;;
    esac
done
if [ "$TCP_SECONDS" -gt 300 ]; then
    printf 'Passive TCP capture is limited to 300 seconds: %s\n' "$TCP_SECONDS" >&2
    exit 2
fi

need() { command -v "$1" >/dev/null 2>&1; }

require_base_install() {
    [ -x "$PYTHON" ] || {
        printf 'OCPP CSMS is not installed at %s. Run sh install.sh first.\n' "$PREFIX" >&2
        exit 1
    }
}

missing_runtime() {
    missing=""
    for command in tcpdump nft ip; do
        if ! need "$command"; then missing="$missing $command"; fi
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
    if [ ! -r /etc/os-release ]; then show_dependency_help; exit 1; fi
    . /etc/os-release
    if [ "${ID:-}" != debian ]; then
        printf 'Automatic dependency installation is supported only on Debian.\n' >&2
        show_dependency_help
        exit 1
    fi
    sudo apt-get update
    sudo apt-get install -y tcpdump nftables iproute2
}

escape_sed() { printf '%s' "$1" | sed 's/[\\&|]/\\&/g'; }

install_discovery() {
    require_base_install
    need sudo || { printf 'Missing required command: sudo\n' >&2; exit 1; }
    need systemctl || { printf 'Missing required command: systemctl\n' >&2; exit 1; }
    install_dependencies

    sudo "$PYTHON" -m ocpp_discover.lifecycle prepare
    # Debian's nftables.service loads /etc/nftables.conf before networking.
    # Enable it for future boots without restarting/flushing the current ruleset.
    sudo systemctl enable nftables.service

    PYTHON_ESC=$(escape_sed "$PYTHON")
    DATA_ESC=$(escape_sed "$DATA_DIR")
    INTERFACE_ESC=$(escape_sed "$INTERFACE")
    TMP_SERVICE=$(mktemp --suffix=.service)
    trap 'rm -f "$TMP_SERVICE"' EXIT HUP INT TERM
    sed \
        -e "s|@PYTHON@|$PYTHON_ESC|g" \
        -e "s|@DATA_DIR@|$DATA_ESC|g" \
        -e "s|@INTERFACE@|$INTERFACE_ESC|g" \
        -e "s|@PORT@|$PORT|g" \
        "$SERVICE_TEMPLATE" > "$TMP_SERVICE"

    if need systemd-analyze; then systemd-analyze verify "$TMP_SERVICE" >/dev/null; fi
    sudo install -m 0644 "$TMP_SERVICE" "$SERVICE_PATH"
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"
    sudo systemctl start --no-block "$SERVICE_NAME"
    printf 'Installed OCPP Discover on %s; service enabled for boot.\n' "$INTERFACE"
}

cleanup_discovery() {
    require_base_install
    sudo "$PYTHON" -m ocpp_discover cleanup --state-dir /run/ocpp-discover
}

uninstall_discovery() {
    require_base_install
    need sudo || { printf 'Missing required command: sudo\n' >&2; exit 1; }
    need systemctl || { printf 'Missing required command: systemctl\n' >&2; exit 1; }
    sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    cleanup_discovery
    sudo "$PYTHON" -m ocpp_discover.lifecycle remove
    sudo systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    sudo rm -f "$SERVICE_PATH"
    sudo systemctl daemon-reload
    printf 'Uninstalled OCPP Discover. OCPP CSMS remains installed.\n'
}

run_discovery() {
    require_base_install
    missing=$(missing_runtime)
    if [ -n "$missing" ]; then show_dependency_help; exit 1; fi
    set -- "$PYTHON" -m ocpp_discover run \
        --data-dir "$DATA_DIR" \
        --state-dir /run/ocpp-discover \
        --interface "$INTERFACE" \
        --listen-port "$PORT" \
        --grace-seconds "$GRACE_SECONDS" \
        --arp-seconds "$ARP_SECONDS" \
        --tcp-seconds "$TCP_SECONDS"
    if [ "$EXISTING_ENDPOINT_ONLY" -eq 1 ]; then set -- "$@" --existing-endpoint-only; fi
    if [ "$DIAGNOSTIC_ONLY" -eq 1 ]; then set -- "$@" --passive-diagnostic-only; fi
    if [ -n "$PASSIVE_CAPTURE_LOG" ]; then set -- "$@" --passive-capture-log "$PASSIVE_CAPTURE_LOG"; fi
    sudo "$@"
}

case "$MODE" in
    run) run_discovery ;;
    install) install_discovery ;;
    uninstall) uninstall_discovery ;;
    cleanup) cleanup_discovery ;;
esac
