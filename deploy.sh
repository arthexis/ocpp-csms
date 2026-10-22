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

playbook="$ROOT/ansible/playbooks/install.yml"
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

# Recover an existing CSMS public hostname only from managed nginx sites.
# Refuse ambiguous installations; never guess from generic system hostnames.
recover_exposure() {
    found=""
    count=0
    for site in /etc/nginx/sites-enabled/*; do
        [ -f "$site" ] || continue
        case "$(basename "$site")" in
            *.*) ;;
            *) continue ;;
        esac
        if grep -q 'proxy_set_header Sec-WebSocket-Protocol' "$site" 2>/dev/null &&
           grep -q 'proxy_pass http://127.0.0.1:' "$site" 2>/dev/null; then
            hostname=$(basename "$site")
            case "$hostname" in
                *[!a-z0-9.-]*|.*|*..*) continue ;;
            esac
            if [ -f "/etc/letsencrypt/live/$hostname/fullchain.pem" ]; then
                found=$hostname
                count=$((count + 1))
            fi
        fi
    done
    if [ "$count" -gt 1 ]; then
        printf 'Multiple managed CSMS nginx sites found; specify --expose HOSTNAME.\n' >&2
        return 2
    fi
    [ "$count" -eq 1 ] && printf '%s' "$found"
    return 0
}

if [ -n "$email" ] || [ "$no_certbot" -eq 1 ]; then
    [ -n "$expose" ] || { printf '%s\n' '--email and --no-certbot require --expose.' >&2; exit 2; }
fi

# On an ordinary reinstall, refresh a previously configured public nginx
# site after the application install; do not create exposure on private hosts.
recovered_expose=""
if [ -z "$expose" ] && [ "$stage_only" -eq 0 ] && [ "$dev" -eq 0 ]; then
    recovered_expose=$(recover_exposure) || exit $?
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

timing_file=$(mktemp)
trap 'rm -f "$timing_file"' EXIT HUP INT TERM
started_at=$(date +%s)
if ansible-playbook "$playbook" -i localhost, -c local -e "ocpp_csms_timing_file=$timing_file" "$@"; then
    status=0
else
    status=$?
fi
duration=$(($(date +%s) - started_at))
printf 'OCPP deployment finished in %ds\n' "$duration"
if [ -s "$timing_file" ]; then
    critical_ms=$(cat "$timing_file")
    case "$critical_ms" in
        *[!0-9]*|'') printf 'OCPP critical handoff time unavailable (invalid measurement)\n' ;;
        *) printf 'OCPP critical handoff time: %d.%03ds (stop requested to verified recovery)\n' "$((critical_ms / 1000))" "$((critical_ms % 1000))" ;;
    esac
else
    printf 'OCPP critical handoff time: n/a (no verified handoff)\n'
fi
if [ "$status" -eq 0 ] && [ -n "$recovered_expose" ]; then
    printf 'Reusing existing public exposure: %s\n' "$recovered_expose"
    if ! ansible-playbook "$ROOT/ansible/playbooks/expose.yml" -i localhost, -c local \
        -e "ocpp_csms_expose=$recovered_expose"; then
        printf 'CSMS install succeeded, but nginx exposure refresh failed.\n' >&2
        status=1
    fi
fi
exit "$status"
