#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ "$(id -u)" -eq 0 ]; then
    printf 'Do not run deploy.sh as root or with sudo.\n' >&2
    exit 1
fi

if ! command -v ansible-playbook >/dev/null 2>&1; then
    printf 'Missing required command: ansible-playbook\n' >&2
    exit 1
fi

playbook="$ROOT/ansible/playbooks/satellite.yml"
expose=""
email=""
no_certbot=0
uninstall=0
dev=0
stage_only=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --expose|--email)
            [ "$#" -ge 2 ] || { printf 'Missing value for %s.\n' "$1" >&2; exit 2; }
            if [ "$1" = --expose ]; then expose=$2; else email=$2; fi
            shift 2 ;;
        --expose=*) expose=${1#--expose=}; shift ;;
        --email=*) email=${1#--email=}; shift ;;
        --no-certbot) no_certbot=1; shift ;;
        --uninstall) uninstall=1; shift ;;
        --dev) dev=1; shift ;;
        --stage-only) stage_only=1; shift ;;
        --) shift; break ;;
        -*) printf 'Unknown deployment option: %s\n' "$1" >&2; exit 2 ;;
        *) break ;;
    esac
done

if [ "$uninstall" -eq 1 ]; then
    if [ -n "$expose" ] || [ -n "$email" ] || [ "$no_certbot" -eq 1 ] || [ "$dev" -eq 1 ] || [ "$stage_only" -eq 1 ] || [ "$#" -gt 0 ]; then
        printf '%s\n' '--uninstall must be used alone.' >&2
        exit 2
    fi
    exec ansible-playbook "$ROOT/ansible/playbooks/uninstall.yml" -i localhost, -c local
fi

if [ -n "$email" ] || [ "$no_certbot" -eq 1 ]; then
    [ -n "$expose" ] || { printf '%s\n' '--email and --no-certbot require --expose.' >&2; exit 2; }
fi

if [ -n "$expose" ]; then
    case "$expose" in
        *[!a-z0-9.-]*|.*|*..*|'') printf 'Invalid public DNS hostname.\n' >&2; exit 2 ;;
        *.*) ;;
        *) printf '%s\n' '--expose requires a fully qualified DNS name.' >&2; exit 2 ;;
    esac
    playbook="$ROOT/ansible/playbooks/expose.yml"
    set -- -e "ocpp_csms_expose=$expose" "$@"
    if [ -n "$email" ]; then set -- -e "ocpp_csms_certbot_email=$email" "$@"; fi
    if [ "$no_certbot" -eq 1 ]; then set -- -e ocpp_csms_no_certbot=true "$@"; fi
fi

if [ "$dev" -eq 1 ]; then set -- -e ocpp_csms_dev=true "$@"; fi
if [ "$stage_only" -eq 1 ]; then set -- -e ocpp_csms_stage_only=true "$@"; fi

started_at=$(date +%s)
if ansible-playbook "$playbook" -i localhost, -c local "$@"; then
    status=0
else
    status=$?
fi
duration=$(($(date +%s) - started_at))
printf 'OCPP deployment finished in %ds\n' "$duration"
exit "$status"
