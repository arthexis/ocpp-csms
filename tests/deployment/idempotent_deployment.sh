#!/usr/bin/env bash
# End-to-end acceptance checks. Run only on an isolated disposable CI runner
# after deploy.sh has installed the service.
set -euo pipefail

root="$HOME/.local/share/ocpp-csms"
storage="$HOME/ocpp-csms-data"
current="$root/current"
unit=ocpp-csms.service

die() { echo "::error::$*" >&2; exit 1; }
pid() { sudo systemctl show "$unit" -p MainPID --value; }
active_data_dir() {
  sudo systemctl show "$unit" -p ExecStart --value |
    grep -oE -- '--data-dir[ =][^ ;}]+' |
    head -1 | sed -E 's/^--data-dir[ =]//'
}

test -L "$current" || die "missing current release symlink"
before_release="$(readlink -f "$current")"
before_pid="$(pid)"
[[ "$before_pid" =~ ^[1-9][0-9]*$ ]] || die "service not running"
before_data="$(active_data_dir)"
test -d "$before_data" || die "cannot locate service storage directory"
test -f "$before_data/ocpp-csms.sqlite3" || die "missing active SQLite database"

# The same-commit no-op must preserve the service, release and actual
# database bytes/identity; an application-owned archive is preserved too.
db_identity="$(stat -c '%d:%i' "$before_data/ocpp-csms.sqlite3")"
mkdir -p "$before_data/transactions"
archive="$before_data/transactions/ci-preservation-probe.json"
printf '{"ci_probe":true}\n' > "$archive"
archive_hash="$(sha256sum "$archive" | cut -d' ' -f1)"
./deploy.sh
test "$(readlink -f "$current")" = "$before_release" || die "same commit switched release"
test "$(pid)" = "$before_pid" || die "same commit restarted service"
test "$(active_data_dir)" = "$before_data" || die "same schema changed data directory"
test "$(stat -c '%d:%i' "$before_data/ocpp-csms.sqlite3")" = "$db_identity" || die "same schema replaced database"
test "$(sha256sum "$archive" | cut -d' ' -f1)" = "$archive_hash" || die "same schema changed archive"

# Safe staging must never perform a live handoff or change database identity.
./deploy.sh --stage-only
test "$(pid)" = "$before_pid" || die "stage-only interrupted service"
test "$(readlink -f "$current")" = "$before_release" || die "stage-only switched release"
test "$(stat -c '%d:%i' "$before_data/ocpp-csms.sqlite3")" = "$db_identity" || die "stage-only replaced database"

# Argument validation must fail before invoking Ansible or interrupting the
# running service. This is not a substitute for an injected Ansible failure.
if ./deploy.sh --nonexistent-ci-option; then
  die "invalid deploy option unexpectedly succeeded"
fi
test "$(pid)" = "$before_pid" || die "invalid argument interrupted service"
test "$(readlink -f "$current")" = "$before_release" || die "invalid argument switched release"

# A damaged *inactive* release is deliberately outside this test: rebuilding
# one requires a second commit and an isolated fixture. Do not corrupt the
# running service or its release on a shared runner.
echo "Same-schema persistence and argument preflight checks passed"
