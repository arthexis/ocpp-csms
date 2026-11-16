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
  git -C "$repo" worktree remove --force "$tmp/pre" >/dev/null 2>&1 || true
  git -C "$repo" worktree remove --force "$tmp/new" >/dev/null 2>&1 || true
  git -C "$repo" worktree remove --force "$tmp/same-schema" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT

sudo ip link add ocppci0 type dummy
sudo ip link set ocppci0 up
[[ -z "$(ip -o -4 addr show dev ocppci0)" ]] || fail "dummy has IPv4 address"
# The GitHub runner's iptables-nft counters change naturally as it downloads
# packages; compare deterministic rule structure, not packet/byte counters.
normalized_ruleset() {
  sudo nft -j -nn list ruleset | python3 -c '
import json,sys
def stable(x):
    if isinstance(x, list):
        return [stable(item) for item in x]
    if isinstance(x, dict):
        return {key: stable(value) for key,value in x.items()
                if key not in ("packets", "bytes", "handle")}
    return x
print(json.dumps(stable(json.load(sys.stdin)), sort_keys=True, separators=(",", ":")))
'
}
assert_no_discover_redirect() {
  if sudo nft list table ip ocpp_field_redirect >/dev/null 2>&1; then
    fail "Discover installed an unexpected live redirect table"
  fi
}
assert_no_discover_redirect
normalized_ruleset >"$tmp/nft-before"

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
sqlite_logical_snapshot() {
  # Baseline with an empty second argument; verify using its JSON file.
  # CSMS legitimately appends server_started/server_stopped audit records.
  python3 - "$1" "$2" <<'PY'
import json
from pathlib import Path
import sqlite3
import sys

def encode(value):
    return {"__blob__": value.hex()} if isinstance(value, bytes) else value

database = Path(sys.argv[1]).resolve(strict=True)
connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)
try:
    connection.execute("PRAGMA query_only=ON")
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("SQLite integrity_check failed")
    tables = [
        name for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name != 'sqlite_sequence' ORDER BY name"
        )
    ]
    state = {
        "version": connection.execute("PRAGMA user_version").fetchone()[0],
        "schema": [list(row) for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name != 'sqlite_sequence' ORDER BY type, name"
        )],
        "tables": {},
    }
    for name in tables:
        quoted = '"' + name.replace('"', '""') + '"'
        rows = [
            [encode(v) for v in row]
            for row in connection.execute("SELECT * FROM " + quoted)
        ]
        state["tables"][name] = sorted(
            rows, key=lambda r: json.dumps(r, sort_keys=True)
        )
finally:
    connection.close()

baseline_path = sys.argv[2]
if not baseline_path:
    print(json.dumps(state, sort_keys=True))
else:
    before = json.loads(Path(baseline_path).read_text())
    if state["version"] != before["version"] or state["schema"] != before["schema"]:
        raise SystemExit("SQLite schema/version changed during pre-writer recovery")
    if set(state["tables"]) != set(before["tables"]):
        raise SystemExit("SQLite tables changed during pre-writer recovery")
    for name in state["tables"]:
        previous = before["tables"][name]
        current = state["tables"][name]
        if name != "runtime_events":
            if current != previous:
                raise SystemExit("SQLite table mutated during pre-writer recovery: " + name)
            continue
        old_by_id = {row[0]: row for row in previous}
        new_by_id = {row[0]: row for row in current}
        if any(new_by_id.get(key) != row for key, row in old_by_id.items()):
            raise SystemExit("Prior runtime_events rows were removed or modified")
        previous_max = max(old_by_id, default=0)
        appended = [row for key, row in new_by_id.items() if key not in old_by_id]
        if any(row[0] <= previous_max or row[2] not in
               ("server_started", "server_stopped") for row in appended):
            raise SystemExit("Unexpected runtime_events written during recovery")
    print("PASS: SQLite integrity, schema and prior evidence preserved; "
          "only CSMS startup/shutdown audit events appended")
PY
}
snapshot() {
  python3 - "$1" <<'PY'
import hashlib
from pathlib import Path
import sqlite3
import sys
database = Path(sys.argv[1]).resolve(strict=True)
connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)
try:
    connection.execute("PRAGMA query_only=ON")
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("SQLite integrity_check failed")
    snapshot = "\n".join(connection.iterdump()).encode("utf-8")
    print(hashlib.sha256(snapshot).hexdigest())
finally:
    connection.close()
PY
}
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
  assert_no_discover_redirect
  normalized_ruleset >"$tmp/nft-after"
  diff -u "$tmp/nft-before" "$tmp/nft-after" ||
    fail "kernel nftables rule structure changed in isolated CI"
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

# A new release with the same schema must replace the running Python process;
# a subsequent deploy of that same release must leave it undisturbed.
git worktree add --detach "$tmp/same-schema" HEAD
git -C "$tmp/same-schema" -c user.name='Stage A CI' -c user.email='ci@example.invalid' \
    commit --allow-empty -qm 'fixture: same-schema release upgrade'
old_pid="$(discover_pid)"
(cd "$tmp/same-schema" && ansible-playbook ansible/playbooks/ci-discover-isolated.yml -i localhost, -c local -e ocpp_stage_a_ci_fixture=true)
assert_active
[[ "$(discover_pid)" != "$old_pid" ]] || fail "same-schema release left old Discover process running"
pid="$(discover_pid)"
(cd "$tmp/same-schema" && ansible-playbook ansible/playbooks/ci-discover-isolated.yml -i localhost, -c local -e ocpp_stage_a_ci_fixture=true)
[[ "$(discover_pid)" == "$pid" ]] || fail "same-release redeploy restarted Discover"
[[ "$(snapshot)" == "$before" ]] || fail "same-schema upgrade changed Discover persistent state"
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

# Exercise failure inside the real CSMS cutover AFTER quiescing Discover
# and stopping the old writer, but BEFORE marking the candidate writer.
# Only a disposable fixture worktree is instrumented, never product code.
old_current="$(readlink -f "$HOME/.local/share/ocpp-csms/current")"
old_csms_unit="$(sudo sha256sum /etc/systemd/system/ocpp-csms.service | cut -d' ' -f1)"
old_discover_unit="$(sudo sha256sum /etc/systemd/system/ocpp-discover.service | cut -d' ' -f1)"
old_db="$HOME/ocpp-csms-data/schemas/$old_schema/ocpp-csms.sqlite3"
old_db_inode="$(stat -c '%d:%i' "$old_db")"
sqlite_logical_snapshot "$old_db" "" >"$tmp/old-sqlite-logical.json"
# Both managed services must be healthy before this fault is injected.
assert_active
git worktree add --detach "$tmp/pre" "$(git -C "$tmp/new" rev-parse HEAD)"
python3 - "$tmp/pre/ansible/roles/ocpp_csms/tasks/cutover.yml" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
source = p.read_text()
anchor = "    - name: Verify OCPP CSMS storage access as systemd service user\n"
assert source.count(anchor) == 1, "unexpected CSMS cutover pre-writer anchor"
failure = (
    "    - name: Inject CI-only pre-writer failure after Discover quiesce\n"
    "      ansible.builtin.fail:\n"
    "        msg: CI_STAGE_A_PRE_WRITER_BOUNDARY\n\n"
)
p.write_text(source.replace(anchor, failure + anchor, 1))
PY
git -C "$tmp/pre" add ansible/roles/ocpp_csms/tasks/cutover.yml
git -C "$tmp/pre" -c user.name='Stage A CI' -c user.email='ci@example.invalid' \
    commit -qm 'fixture: fail pre-writer after Discover quiescence'

if (cd "$tmp/pre" && ansible-playbook ansible/playbooks/ci-discover-isolated.yml \
    -i localhost, -c local -e ocpp_stage_a_ci_fixture=true) >"$tmp/prewriter.log" 2>&1; then
  fail "pre-writer fault unexpectedly succeeded"
fi
grep -F CI_STAGE_A_PRE_WRITER_BOUNDARY "$tmp/prewriter.log" >/dev/null || {
  tail -n 100 "$tmp/prewriter.log" >&2
  fail "pre-writer injection not reached"
}
grep -F 'OCPP CSMS handoff failed before activation' "$tmp/prewriter.log" >/dev/null || {
  tail -n 100 "$tmp/prewriter.log" >&2
  fail "old-CSMS recovery path did not complete"
}
# Verify both actual systemd services recovered, not just a role's YAML order.
assert_active
[[ "$(csms_dir)" == "$HOME/ocpp-csms-data/schemas/$old_schema" ]] ||
  fail "pre-writer recovery activated the wrong schema"
[[ "$(readlink -f "$HOME/.local/share/ocpp-csms/current")" == "$old_current" ]] ||
  fail "pre-writer recovery changed the active release"
[[ "$(sudo sha256sum /etc/systemd/system/ocpp-csms.service | cut -d' ' -f1)" == "$old_csms_unit" ]] ||
  fail "pre-writer recovery changed the old CSMS unit"
[[ "$(sudo sha256sum /etc/systemd/system/ocpp-discover.service | cut -d' ' -f1)" == "$old_discover_unit" ]] ||
  fail "pre-writer recovery changed the old Discover unit"
[[ "$(stat -c '%d:%i' "$old_db")" == "$old_db_inode" ]] ||
  fail "pre-writer recovery replaced the old SQLite database"
sqlite_logical_snapshot "$old_db" "$tmp/old-sqlite-logical.json" ||
  fail "pre-writer recovery changed existing SQLite evidence or integrity"
[[ "$(snapshot)" == "$before" ]] || fail "pre-writer recovery modified Discover receipts"
assert_nft_unchanged
echo "PASS: pre-writer fault after active Discover quiesce restores CSMS and Discover, old schema, SQLite inode/prior evidence, receipts and nftables"

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
