#!/usr/bin/env bash
# CI-only real systemd/Ansible lifecycle on GitHub-hosted disposable VM.
# Only a dummy unaddressed ocppci0 is used; no chargers or real interface.
set -euo pipefail
[[ "$GITHUB_ACTIONS" == true && "$RUNNER_ENVIRONMENT" == github-hosted ]] || exit 2
[[ "$(id -u)" != 0 ]] || exit 2
fail() { echo "::error::$*" >&2; exit 1; }
if ip link show dev ocppci0 >/dev/null 2>&1; then
  fail "existing ocppci0 is not a disposable dummy; refusing to run"
fi
[[ ! -e /etc/systemd/system/ocpp-discover.service ]] ||
  fail "preexisting Discover unit; refusing nonfresh runner"
repo="$(git rev-parse --show-toplevel)"
export OCPP_STAGE_A_TEST_INTERFACE=ocppci0
tmp="$(mktemp -d /tmp/ocpp-stage-a.XXXXXX)"
cleanup() {
  sudo systemctl stop ocpp-discover.service >/dev/null 2>&1 || true
  sudo systemctl reset-failed ocpp-discover.service >/dev/null 2>&1 || true
  sudo ip link del ocppci0 >/dev/null 2>&1 || true
  git -C "$repo" worktree remove --force "$tmp/new" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT

sudo ip link add ocppci0 type dummy
sudo ip link set ocppci0 up
[[ -z "$(ip -o -4 addr show dev ocppci0)" ]] || fail "dummy has IPv4 address"
sudo nft -nn list ruleset >"$tmp/nft-before"

schema_from_source() {
  python3 - "$1" <<'PY'
import ast,sys
from pathlib import Path
for n in ast.parse(Path(sys.argv[1]).read_text()).body:
    if isinstance(n,ast.Assign) and any(
        isinstance(t,ast.Name) and t.id == "CURRENT_SCHEMA_VERSION" for t in n.targets):
        print(ast.literal_eval(n.value));break
else:
    raise SystemExit("missing schema")
PY
}
csms_dir() {
  sudo systemctl show ocpp-csms.service -p ExecStart --value |
    grep -oE -- '--data-dir[ =][^ ;}]+' | head -1 | sed -E 's/^--data-dir[ =]//'
}
discover_pid() { sudo systemctl show ocpp-discover.service -p MainPID --value; }
snapshot() {
  sudo python3 - <<'PY'
from hashlib import sha256
from pathlib import Path
paths = [Path("/etc/nftables.conf")]
for root in (Path("/var/lib/ocpp-discover"), Path("/etc/ocpp-discover")):
    if root.exists():
        paths.extend(x for x in root.rglob("*") if x.is_file())
for path in sorted(paths):
    print(path, sha256(path.read_bytes()).hexdigest())
PY
}
assert_nft_unchanged() {
  sudo nft -nn list ruleset >"$tmp/nft-after"
  cmp "$tmp/nft-before" "$tmp/nft-after" || fail "kernel nftables changes detected"
}
assert_active() {
  sudo systemctl is-active --quiet ocpp-csms.service || fail "CSMS is inactive"
  sudo systemctl is-active --quiet ocpp-discover.service || fail "Discover is inactive"
  [[ "$(discover_pid)" =~ ^[1-9][0-9]*$ ]] || fail "Discover MainPID missing"
  python3 tests/deployment/discover_storage_contract.py
  sleep 1
  sudo systemctl is-active --quiet ocpp-discover.service ||
    fail "Discover failed after startup"
}

# Install actual roles on disposable dummy ocppci0, then prove same-sha no-op.
ansible-playbook ansible/playbooks/ci-discover-isolated.yml -i localhost, -c local -e ocpp_stage_a_ci_fixture=true
assert_active
old_schema="$(schema_from_source src/ocpp_csms/schema.py)"
[[ "$(csms_dir)" == "$HOME/ocpp-csms-data/schemas/$old_schema" ]] ||
  fail "initial schema mismatch"
before="$(snapshot)"
pid="$(discover_pid)"
ansible-playbook ansible/playbooks/ci-discover-isolated.yml -i localhost, -c local -e ocpp_stage_a_ci_fixture=true
assert_active
[[ "$(discover_pid)" == "$pid" ]] || fail "Discover restarted in no-op redeploy"
[[ "$(snapshot)" == "$before" ]] || fail "no-op modified Discover persistent state"
assert_nft_unchanged

# Actual systemd-managed positive restart, not a mocked subprocess.
sudo systemctl restart ocpp-discover.service
assert_active
[[ "$(snapshot)" == "$before" ]] || fail "restart changed persistent state"

# Synthetic schema advancement in separate immutable CI-only worktree.
git worktree add --detach "$tmp/new" HEAD
python3 - "$tmp/new/src/ocpp_csms/schema.py" <<'PY'
import ast,sys
from pathlib import Path
p = Path(sys.argv[1]);s = p.read_text()
matches = [n for n in ast.parse(s).body if isinstance(n,ast.Assign)
           and any(isinstance(t,ast.Name) and t.id=="CURRENT_SCHEMA_VERSION"
                   for t in n.targets)]
assert len(matches) == 1
old = ast.literal_eval(matches[0].value)
assert isinstance(old,int) and old > 0
needle = f"CURRENT_SCHEMA_VERSION = {old}"
assert s.count(needle) == 1
p.write_text(s.replace(needle,f"CURRENT_SCHEMA_VERSION = {old+1}",1))
PY
git -C "$tmp/new" add src/ocpp_csms/schema.py
git -C "$tmp/new" -c user.name='Stage A CI' -c user.email='ci@example.invalid' \
    commit -qm 'fixture: schema increment'
new_schema="$(schema_from_source "$tmp/new/src/ocpp_csms/schema.py")"
old_unit="$(sudo cat /etc/systemd/system/ocpp-discover.service)"
[[ "$old_unit" != *"/schemas/$new_schema"* ]] || fail "unexpected new-schema Discover unit"

# Deliberately fail between successful CSMS writer handoff and Discover role.
if (cd "$tmp/new" && ansible-playbook ansible/playbooks/ci-discover-postwriter.yml \
    -i localhost, -c local -e ocpp_stage_a_ci_fixture=true) >"$tmp/fault.log" 2>&1; then
  fail "postwriter fixture unexpectedly succeeded"
fi
grep -F CI_STAGE_A_POST_WRITER_BOUNDARY "$tmp/fault.log" >/dev/null || {
  tail -n 80 "$tmp/fault.log" >&2
  fail "fixture failed before reaching postwriter boundary"
}
[[ "$(csms_dir)" == "$HOME/ocpp-csms-data/schemas/$new_schema" ]] ||
  fail "candidate writer not active on new schema"
sudo systemctl is-active --quiet ocpp-csms.service || fail "new CSMS inactive"
if sudo systemctl is-active --quiet ocpp-discover.service; then
  fail "Discover still active on old schema after handoff"
fi
[[ "$(sudo cat /etc/systemd/system/ocpp-discover.service)" == "$old_unit" ]] ||
  fail "postwriter fault changed Discover unit"
[[ "$(snapshot)" == "$before" ]] || fail "postwriter fault changed durable state"
assert_nft_unchanged

# The obsolete actual unit must refuse a new start via ExecStartPre.
if timeout 20s sudo systemctl start ocpp-discover.service; then
  fail "obsolete Discover unit restarted against new CSMS schema"
fi
sudo systemctl stop ocpp-discover.service || true
sudo systemctl reset-failed ocpp-discover.service || true
[[ "$(discover_pid)" == "0" ]] || fail "old Discover process remains"
[[ "$(snapshot)" == "$before" ]] || fail "negative restart changed persistent state"

# Forward repair by the normal product installer at the candidate schema.
(cd "$tmp/new" && ansible-playbook ansible/playbooks/ci-discover-isolated.yml -i localhost, -c local -e ocpp_stage_a_ci_fixture=true)
[[ "$(csms_dir)" == "$HOME/ocpp-csms-data/schemas/$new_schema" ]] ||
  fail "forward repair changed CSMS schema unexpectedly"
sudo systemctl is-active --quiet ocpp-discover.service || fail "Discover not restored"
[[ "$(sudo cat /etc/systemd/system/ocpp-discover.service)" == *"/schemas/$new_schema"* ]] ||
  fail "Discover unit did not advance schema"
sudo systemctl restart ocpp-discover.service
sudo systemctl is-active --quiet ocpp-discover.service || fail "advanced Discover restart failed"
(cd "$tmp/new" && python3 tests/deployment/discover_storage_contract.py)
[[ "$(snapshot)" == "$before" ]] || fail "forward repair changed durable state"
assert_nft_unchanged
echo "PASS: real Discover Ansible/systemd first install, restart, idempotence, old-schema fail-closed, postwriter fail-forward and persistent/network-state preservation on dummy CI ocppci0"
