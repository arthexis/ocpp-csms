#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PLAYBOOK="$ROOT/ansible/playbooks/satellite.yml"

if [ "$(id -u)" -eq 0 ]; then
    printf 'Do not run ansible-deploy.sh as root or with sudo.\n' >&2
    exit 1
fi

if ! command -v ansible-playbook >/dev/null 2>&1; then
    printf 'Missing required command: ansible-playbook\n' >&2
    printf 'Install Ansible before running this deployment helper.\n' >&2
    exit 1
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
