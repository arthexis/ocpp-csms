#!/bin/sh
set -eu

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
MODE=run

usage() {
    cat <<'EOF'
Usage: sh discover.sh [status|diagnostics|run|cleanup] [options]

Compatibility wrapper for the installed `ocpp-discover` command.
Installation and service ownership belong to `install.sh` or Ansible.
A command-less invocation retains the historical explicit `run` behavior.

Commands:
  status             Show read-only Discover service/persistence status.
  diagnostics        Inspect the proven adaptation and nftables state read-only.
  run                Run an explicit manual discovery attempt (may mutate owned network state).
  cleanup            Remove transient Discover-owned network state; keep durable adaptation.

Run options:
  --interface IFACE  Charger-facing Ethernet interface (default: eth0).
  --grace-seconds N  Wait for a live charger before capture (default: 10).
  --arp-seconds N    ARP capture duration if fallback is enabled (default: 15).
  --tcp-seconds N    Passive TCP capture duration (default: 15; max: 300).
  --existing-endpoint-only  Do not fall back to ARP or claim an address.
  --diagnostic-only  Observe an existing endpoint without network mutation.
  --passive-capture-log PATH  Save the passive TCP transcript to a new local file.
  -h, --help         Show this help.

`--install` and `--uninstall` are no longer supported. Discover is part of the
OCPP appliance and is installed together with CSMS.
EOF
}

case "${1:-}" in
    status|diagnostics|run|cleanup) MODE=$1; shift ;;
    --install|--uninstall)
        printf 'discover.sh %s is no longer supported; Discover is installed with the OCPP appliance. Run sh install.sh (legacy) or the Ansible satellite playbook.\n' "$1" >&2
        exit 2
        ;;
    -h|--help) usage; exit 0 ;;
    '') ;;
esac

while [ "$#" -gt 0 ]; do
    case "$1" in
        --interface)
            [ "$#" -ge 2 ] || { printf 'Missing value for --interface\n' >&2; exit 2; }
            INTERFACE=$2; shift 2 ;;
        --interface=*) INTERFACE=${1#*=}; shift ;;
        --grace-seconds|--arp-seconds|--tcp-seconds|--passive-capture-log)
            [ "$#" -ge 2 ] || { printf 'Missing value for %s\n' "$1" >&2; exit 2; }
            case "$1" in
                --grace-seconds) GRACE_SECONDS=$2 ;;
                --arp-seconds) ARP_SECONDS=$2 ;;
                --tcp-seconds) TCP_SECONDS=$2 ;;
                --passive-capture-log) PASSIVE_CAPTURE_LOG=$2 ;;
            esac
            shift 2 ;;
        --grace-seconds=*|--arp-seconds=*|--tcp-seconds=*|--passive-capture-log=*)
            value=${1#*=}
            case "$1" in
                --grace-seconds=*) GRACE_SECONDS=$value ;;
                --arp-seconds=*) ARP_SECONDS=$value ;;
                --tcp-seconds=*) TCP_SECONDS=$value ;;
                --passive-capture-log=*) PASSIVE_CAPTURE_LOG=$value ;;
            esac
            shift ;;
        --existing-endpoint-only) EXISTING_ENDPOINT_ONLY=1; shift ;;
        --diagnostic-only) DIAGNOSTIC_ONLY=1; EXISTING_ENDPOINT_ONLY=1; shift ;;
        --install|--uninstall)
            printf 'discover.sh %s is no longer supported; install the whole OCPP appliance instead.\n' "$1" >&2
            exit 2 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

case "$PORT" in ''|*[!0-9]*) printf 'OCPP listener port must be an integer: %s\n' "$PORT" >&2; exit 2 ;; esac
if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
    printf 'OCPP listener port must be between 1 and 65535: %s\n' "$PORT" >&2
    exit 2
fi
for value in "$GRACE_SECONDS" "$ARP_SECONDS" "$TCP_SECONDS"; do
    case "$value" in ''|*[!0-9]*) printf 'Discovery durations must be non-negative integer seconds: %s\n' "$value" >&2; exit 2 ;; esac
done
if [ "$TCP_SECONDS" -gt 300 ]; then
    printf 'Passive TCP capture is limited to 300 seconds: %s\n' "$TCP_SECONDS" >&2
    exit 2
fi

resolve_command() {
    if command -v ocpp-discover >/dev/null 2>&1; then
        command -v ocpp-discover
        return
    fi
    if [ -x "$PREFIX/venv/bin/ocpp-discover" ]; then
        printf '%s\n' "$PREFIX/venv/bin/ocpp-discover"
        return
    fi
    printf 'OCPP Discover is not installed. Run sh install.sh or converge the Ansible satellite playbook.\n' >&2
    exit 1
}

DISCOVER=$(resolve_command)

case "$MODE" in
    status)
        exec "$DISCOVER" status
        ;;
    diagnostics)
        exec "$DISCOVER" diagnostics
        ;;
    cleanup)
        exec sudo "$DISCOVER" cleanup --state-dir /run/ocpp-discover
        ;;
    run)
        set -- "$DISCOVER" run \
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
        exec sudo "$@"
        ;;
esac
