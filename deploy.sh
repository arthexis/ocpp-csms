#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PLAYBOOK="$ROOT/ansible/playbooks/satellite.yml"
DIAGNOSE_PLAYBOOK="$ROOT/ansible/playbooks/diagnose.yml"

if [ "$(id -u)" -eq 0 ]; then
    printf 'Do not run deploy.sh as root or with sudo.\n' >&2
    exit 1
fi

if ! command -v ansible-playbook >/dev/null 2>&1; then
    printf 'Missing required command: ansible-playbook\n' >&2
    printf 'Install Ansible before running this deployment helper.\n' >&2
    exit 1
fi

observe_seconds=""
interface=""
reconnect_seconds=""
stage_only=0
diagnose=0
dev=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --observe)
            [ "$#" -ge 2 ] || { printf 'Missing value for --observe.\n' >&2; exit 2; }
            observe_seconds=$2
            shift 2
            ;;
        --observe=*)
            observe_seconds=${1#--observe=}
            shift
            ;;
        --interface)
            [ "$#" -ge 2 ] || { printf 'Missing value for --interface.\n' >&2; exit 2; }
            interface=$2
            shift 2
            ;;
        --interface=*)
            interface=${1#--interface=}
            shift
            ;;
        --reconnect)
            [ "$#" -ge 2 ] || { printf 'Missing value for --reconnect.\n' >&2; exit 2; }
            reconnect_seconds=$2
            shift 2
            ;;
        --reconnect=*)
            reconnect_seconds=${1#--reconnect=}
            shift
            ;;
        --stage-only)
            stage_only=1
            shift
            ;;
        --diagnose)
            diagnose=1
            shift
            ;;
        --dev)
            dev=1
            shift
            ;;
        --)
            shift
            break
            ;;
        *)
            break
            ;;
    esac
done

validate_positive_seconds() {
    option=$1
    value=$2
    case "$value" in
        *[!0-9]*|'')
            printf '%s must be a positive integer number of seconds.\n' "$option" >&2
            exit 2
            ;;
        0)
            printf '%s must be greater than zero.\n' "$option" >&2
            exit 2
            ;;
    esac
}

if [ -n "$observe_seconds" ]; then
    validate_positive_seconds --observe "$observe_seconds"
    set -- -e "ocpp_csms_incumbent_observe_seconds=$observe_seconds" "$@"
fi

if [ -n "$interface" ]; then
    case "$interface" in
        *[!A-Za-z0-9_.:-]*|'')
            printf 'Invalid --interface value: %s\n' "$interface" >&2
            exit 2
            ;;
    esac
    set -- -e "ocpp_discover_interface=$interface" "$@"
fi

if [ -n "$reconnect_seconds" ]; then
    validate_positive_seconds --reconnect "$reconnect_seconds"
    set -- -e "ocpp_csms_reconnect_timeout=$reconnect_seconds" "$@"
fi

if [ "$dev" -eq 1 ]; then
    set -- -e ocpp_csms_dev=true "$@"
fi

if [ "$diagnose" -eq 1 ]; then
    if [ "$stage_only" -eq 1 ]; then
        printf '%s\n' '--diagnose and --stage-only cannot be combined.' >&2
        exit 2
    fi
    if [ -n "$reconnect_seconds" ]; then
        printf '%s\n' '--reconnect does not apply to --diagnose.' >&2
        exit 2
    fi
    PLAYBOOK=$DIAGNOSE_PLAYBOOK
elif [ "$stage_only" -eq 1 ]; then
    set -- -e ocpp_csms_stage_only=true "$@"
fi

started_at=$(date +%s)

if ansible-playbook "$PLAYBOOK" -i localhost, -c local "$@"; then
    status=0
else
    status=$?
fi

finished_at=$(date +%s)
duration=$((finished_at - started_at))
minutes=$((duration / 60))
seconds=$((duration % 60))

if [ "$minutes" -gt 0 ]; then
    printf '\nOCPP CSMS deploy finished in %dm %02ds\n' "$minutes" "$seconds"
else
    printf '\nOCPP CSMS deploy finished in %ds\n' "$seconds"
fi

exit "$status"
