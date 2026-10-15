#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PLAYBOOK="$ROOT/ansible/playbooks/satellite.yml"

if [ "$(id -u)" -eq 0 ]; then
    printf 'Do not run deploy.sh as root or with sudo.\n' >&2
    exit 1
fi

if ! command -v ansible-playbook >/dev/null 2>&1; then
    printf 'Missing required command: ansible-playbook\n' >&2
    printf 'Install Ansible before running this deployment helper.\n' >&2
    exit 1
fi

interface=""
stage_only=0
dev=0
expose=""
delegate=0
satellite=0
certbot_email=""
no_certbot=0
adopt=0
uninstall=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --interface)
            [ "$#" -ge 2 ] || { printf 'Missing value for --interface.\n' >&2; exit 2; }
            interface=$2
            shift 2
            ;;
        --interface=*)
            interface=${1#--interface=}
            shift
            ;;
        --stage-only)
            stage_only=1
            shift
            ;;
        --dev)
            dev=1
            shift
            ;;
        --satellite)
            satellite=1
            shift
            ;;
        --delegate)
            delegate=1
            shift
            ;;
        --expose)
            [ "$#" -ge 2 ] || { printf 'Missing DNS hostname for --expose.\n' >&2; exit 2; }
            expose=$2
            shift 2
            ;;
        --expose=*)
            expose=${1#--expose=}
            shift
            ;;
        --email)
            [ "$#" -ge 2 ] || { printf 'Missing value for --email.\n' >&2; exit 2; }
            certbot_email=$2
            shift 2
            ;;
        --email=*)
            certbot_email=${1#--email=}
            shift
            ;;
        --no-certbot)
            no_certbot=1
            shift
            ;;
        --adopt)
            adopt=1
            shift
            ;;
        --uninstall)
            uninstall=1
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

# Deployment identity is immutable until a successful uninstall.
# Resolve it before choosing playbooks so even a flagless invocation is safe.
profile_path="${OCPP_CSMS_DEPLOY_PROFILE_PATH:-$HOME/.local/state/ocpp-csms/deployment.json}"
profile_helper="$ROOT/scripts/deployment_profile.py"
if [ "$uninstall" -eq 1 ]; then
    if [ ! -f "$profile_path" ]; then
        printf 'No recorded installation to uninstall: %s\\n' "$profile_path" >&2
        exit 2
    fi
    if [ "$satellite" -eq 1 ] || [ "$delegate" -eq 1 ] || [ -n "$expose" ] || [ "$stage_only" -eq 1 ]; then
        printf '%s\\n' '--uninstall must be used alone (no deployment or maintenance flags).' >&2
        exit 2
    fi
fi
profile_assignments=$(python3 "$profile_helper" resolve --path "$profile_path" \
    --satellite "$satellite" --delegate "$delegate" \
    --expose "$expose" --email "$certbot_email" \
    --no-certbot "$no_certbot" --interface "$interface" \
    --adopt "$adopt") || exit 2
eval "$profile_assignments"
satellite=0
delegate=0
if [ "$profile_mode" = delegate ]; then
    delegate=1
else
    satellite=1
fi
expose=$profile_expose
certbot_email=$profile_email
no_certbot=$profile_no_certbot
interface=$profile_interface

if [ "$uninstall" -eq 1 ]; then
    PLAYBOOK="$ROOT/ansible/playbooks/uninstall.yml"
    if ansible-playbook "$PLAYBOOK" -i localhost, -c local \
      -e "ocpp_csms_expose=$expose"; then
        python3 "$profile_helper" clear --path "$profile_path"
        printf 'OCPP CSMS uninstalled; deployment profile cleared. Application data preserved.\\n'
        exit 0
    fi
    printf '%s\\n' 'Uninstall failed; deployment profile preserved.' >&2
    exit 1
fi

if [ "$satellite" -eq 1 ] && { [ "$delegate" -eq 1 ] || [ -n "$expose" ]; }; then
    printf '%s\n' '--satellite cannot be combined with --delegate or --expose.' >&2
    exit 2
fi

if [ "$delegate" -eq 1 ]; then
    PLAYBOOK="$ROOT/ansible/playbooks/delegate.yml"
    set -- -e ocpp_csms_host=127.0.0.1 "$@"
fi

if [ -n "$expose" ]; then
    case "$expose" in
        *[!a-z0-9.-]*|.*|*..*|"")
            printf 'Invalid public DNS hostname for --expose: %s\n' "$expose" >&2
            exit 2
            ;;
    esac
    case "$expose" in
        *.*) ;;
        *) printf '%s\n' '--expose requires a fully qualified DNS name.' >&2; exit 2 ;;
    esac
    PLAYBOOK="$ROOT/ansible/playbooks/delegate.yml"
    if [ -n "$certbot_email" ]; then
        set -- -e "ocpp_csms_certbot_email=$certbot_email" "$@"
    fi
    if [ "$no_certbot" -eq 1 ]; then
        set -- -e ocpp_csms_no_certbot=true "$@"
    fi
    set -- -e "ocpp_csms_expose=$expose" -e ocpp_csms_host=127.0.0.1 "$@"
fi

if [ -z "$expose" ] && { [ -n "$certbot_email" ] || [ "$no_certbot" -eq 1 ]; }; then
    printf '%s\n' '--email and --no-certbot require --expose.' >&2
    exit 2
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

if [ "$dev" -eq 1 ]; then
    set -- -e ocpp_csms_dev=true "$@"
fi

if [ "$stage_only" -eq 1 ]; then
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

# Only successful normal deployments update durable intent; stage-only invocations do not.
if [ "$status" -eq 0 ] && [ "$stage_only" -eq 0 ]; then
    commit_delegate=$delegate
    if [ -n "$expose" ]; then
        commit_delegate=0
    fi
    python3 "$profile_helper" commit --path "$profile_path" \
        --satellite "$((1 - delegate))" --delegate "$commit_delegate" \
        --expose "$expose" --email "$certbot_email" \
        --no-certbot "$no_certbot" --interface "$interface" \
        --adopt 1 || exit 2
fi

exit "$status"
