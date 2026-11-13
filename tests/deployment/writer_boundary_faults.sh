#!/usr/bin/env bash
# Pre-writer recovery and post-writer no-rollback contracts, on disposable GitHub runners.
set -euo pipefail
[[ "$GITHUB_ACTIONS" == true && "$RUNNER_ENVIRONMENT" == github-hosted ]] || exit 2
action="$1"
[[ "$action" == reuse || "$action" == create ]] || exit 2
repo="$(git rev-parse --show-toplevel)"
owner="$HOME/.local/share/ocpp-csms"
current="$(readlink -f "$owner/current")"
before_unit="$(sudo sha256sum /etc/systemd/system/ocpp-csms.service | cut -d' ' -f1)"
schema="$(python3 - src/ocpp_csms/schema.py <<'PY'
import ast,sys
for node in ast.parse(open(sys.argv[1]).read()).body:
    if isinstance(node,ast.Assign) and any(
        isinstance(t,ast.Name) and t.id=="CURRENT_SCHEMA_VERSION" for t in node.targets):
        print(ast.literal_eval(node.value));break
else:
    raise SystemExit("missing schema")
PY
)"
db="$HOME/ocpp-csms-data/schemas/$schema/ocpp-csms.sqlite3"
test -f "$db"
db_id="$(stat -c '%d:%i' "$db")"
tmp="$(mktemp -d /tmp/ocpp-c-cutover.XXXXXX)"
cleanup() {
  git -C "$repo" worktree remove --force "$tmp/prewriter" >/dev/null 2>&1 || true
  git -C "$repo" worktree remove --force "$tmp/postwriter" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT

fixture() {
  kind="$1"
  git worktree add --detach "$tmp/$kind" HEAD
  python3 - "$tmp/$kind" "$kind" "$action" <<'PY'
from pathlib import Path
import ast
import sys
root, kind, action = Path(sys.argv[1]),sys.argv[2],sys.argv[3]
file = root / "ansible/roles/ocpp_csms/tasks/cutover.yml"
s = file.read_text()
needle = ("    - name: Verify OCPP CSMS storage access as systemd service user\n"
          if kind == "prewriter"
          else "    - name: Wait for OCPP CSMS listener\n")
assert s.count(needle) == 1
sentinel = "CI_PRE_WRITER_FAILURE" if kind == "prewriter" else "CI_POST_WRITER_FAILURE"
injection = ("    - name: Inject failure at candidate activation boundary\n"
             "      ansible.builtin.fail:\n"
             "        msg: " + sentinel + "\n\n")
file.write_text(s.replace(needle, injection + needle, 1))
if action == "create":
    schema = root / "src/ocpp_csms/schema.py"
    value = schema.read_text()
    candidates = [t for t in ast.parse(value).body if isinstance(t, ast.Assign)
                  and any(isinstance(v,ast.Name) and v.id=="CURRENT_SCHEMA_VERSION"
                          for v in t.targets)]
    assert len(candidates) == 1
    old = ast.literal_eval(candidates[0].value)
    assert isinstance(old,int)
    needle = f"CURRENT_SCHEMA_VERSION = {old}"
    assert value.count(needle) == 1
    schema.write_text(value.replace(needle,f"CURRENT_SCHEMA_VERSION = {old+1}",1))
PY
  git -C "$tmp/$kind" add ansible/roles/ocpp_csms/tasks/cutover.yml
  if [[ "$action" == create ]]; then
    git -C "$tmp/$kind" add src/ocpp_csms/schema.py
  fi
  git -C "$tmp/$kind" -c user.name="CI Fixture" -c user.email="ci@example.invalid" commit -qm "fixture: $kind $action deployment fault"
}

if [[ "$action" == reuse ]]; then
  fixture prewriter
  if (cd "$tmp/prewriter" && ./deploy.sh) >"$tmp/prewriter.log" 2>&1; then
    echo "::error::pre-writer failure unexpectedly passed" >&2; exit 1
  fi
  grep -F CI_PRE_WRITER_FAILURE "$tmp/prewriter.log" >/dev/null || {
    cat "$tmp/prewriter.log"; echo "::error::pre-writer fixture failed for unrelated reason" >&2; exit 1;
  }
  current_after="$(readlink -f "$owner/current")"
  unit_after="$(sudo sha256sum /etc/systemd/system/ocpp-csms.service | cut -d' ' -f1)"
  db_id_after="$(stat -c '%d:%i' "$db")"
  if [[ "$current_after" != "$current" ]]; then
    echo "::error::pre-writer failure changed current pointer: expected=$current actual=$current_after" >&2; exit 1
  fi
  if [[ "$unit_after" != "$before_unit" ]]; then
    echo "::error::pre-writer failure changed systemd unit: expected=$before_unit actual=$unit_after" >&2; exit 1
  fi
  sudo systemctl show ocpp-csms.service -p ExecStart --value | grep -F "$current/venv/bin/ocpp-csms" >/dev/null || {
    echo "::error::pre-writer failure retained unexpected systemd executable" >&2; exit 1;
  }
  if [[ "$db_id_after" != "$db_id" ]]; then
    echo "::error::pre-writer failure changed SQLite identity: expected=$db_id actual=$db_id_after" >&2; exit 1
  fi
  sudo systemctl is-active --quiet ocpp-csms.service || {
    sudo systemctl status ocpp-csms.service --no-pager >&2; exit 1;
  }
  # systemd may report active before the restored control socket is ready.
  healthy=0
  for attempt in $(seq 1 25); do
    if /usr/local/bin/ocpp-csms status >/dev/null 2>&1; then
      healthy=1
      break
    fi
    sleep 0.2
  done
  [[ "$healthy" == 1 ]] || {
    echo "::error::previous service did not regain application status after pre-writer failure" >&2
    /usr/local/bin/ocpp-csms status >&2 || true
    exit 1
  }
  echo "PASS: pre-writer failure restores old unit, release, SQLite and health"
fi

fixture postwriter
candidate_id="$(git -C "$tmp/postwriter" rev-parse --short=12 HEAD)"
if (cd "$tmp/postwriter" && ./deploy.sh) >"$tmp/postwriter.log" 2>&1; then
  echo "::error::post-start injected failure unexpectedly passed" >&2; exit 1
fi
grep -F CI_POST_WRITER_FAILURE "$tmp/postwriter.log" >/dev/null || {
  cat "$tmp/postwriter.log"; echo "::error::post-start fixture failed for unrelated reason" >&2; exit 1;
}
grep -F 'Candidate writer started; preserve database and release for forward repair' "$tmp/postwriter.log" >/dev/null || {
  cat "$tmp/postwriter.log"; echo "::error::automatic rollback after writer start was incorrectly allowed" >&2; exit 1;
}
test "$(readlink -f "$owner/current")" = "$current"
candidate_path="$owner/releases/$candidate_id/venv/bin/ocpp-csms"
sudo systemctl show ocpp-csms.service -p ExecStart --value | grep -F "$candidate_path"
test "$(stat -c '%d:%i' "$db")" = "$db_id"
sudo systemctl is-active --quiet ocpp-csms.service
echo "PASS ($action): candidate may have written; automatic rollback to old unit is prohibited"
