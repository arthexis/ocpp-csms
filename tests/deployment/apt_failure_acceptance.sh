#!/usr/bin/env bash
set -euo pipefail

# Run only on the disposable GitHub Actions runner, after integration.sh has
# installed and verified a healthy CSMS. Never run against a field appliance.
[[ "${GITHUB_ACTIONS:-}" == "true" && "${RUNNER_ENVIRONMENT:-}" == "github-hosted" ]] || {
  echo "APT fault acceptance requires a GitHub-hosted disposable runner" >&2
  exit 2
}
root="$(git rev-parse --show-toplevel)"
fixture="$(mktemp -d /tmp/ocpp-csms-apt-failure.XXXXXX)"
trap 'git -C "$root" worktree remove --force "$fixture" 2>/dev/null || true' EXIT
git worktree add --detach "$fixture" HEAD

# Inject an impossible package into the *real* early APT task in a throwaway
# checkout. The real deploy.sh and Ansible execution remain unmodified.
python3 - "$fixture/ansible/playbooks/install.yml" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
source = p.read_text()
needle = "'python3', 'python3-venv', 'python3-pip', 'iproute2', 'tcpdump'"
assert source.count(needle) == 1
p.write_text(source.replace(needle, needle + ", 'ocpp-csms-ci-nonexistent-package-284'"))
PY

release_path="$HOME/.local/share/ocpp-csms/current"
db="$HOME/ocpp-csms-data/schemas/$(python3 - <<'PY'
import ast
from pathlib import Path
for node in ast.parse(Path("src/ocpp_csms/schema.py").read_text()).body:
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CURRENT_SCHEMA_VERSION" for t in node.targets):
        print(ast.literal_eval(node.value))
        break
else:
    raise SystemExit("schema missing")
PY
)/ocpp-csms.sqlite3"
test -f "$db"
before_release="$(readlink -f "$release_path")"
before_pid="$(sudo systemctl show ocpp-csms.service -p MainPID --value)"
before_unit="$(sudo systemctl show ocpp-csms.service -p ExecStart --value)"
before_inode="$(stat -c '%d:%i' "$db")"
[[ "$before_pid" =~ ^[1-9][0-9]*$ ]]
sudo systemctl is-active --quiet ocpp-csms.service

log="$(mktemp)"
trap 'rm -f "$log"; git -C "$root" worktree remove --force "$fixture" 2>/dev/null || true' EXIT
if (cd "$fixture" && ./deploy.sh) >"$log" 2>&1; then
  echo "::error::APT fault unexpectedly allowed deployment" >&2
  exit 1
fi
# Require a meaningful APT failure, not a false positive from another task.
grep -F 'ocpp-csms-ci-nonexistent-package-284' "$log" >/dev/null || {
  cat "$log"; echo "::error::Missing injected package in failure receipt" >&2; exit 1;
}
grep -E 'No package matching|Unable to locate package|Could not find|not available' "$log" >/dev/null || {
  cat "$log"; echo "::error::Missing actual APT failure detail" >&2; exit 1;
}
test "$(readlink -f "$release_path")" = "$before_release"
test "$(sudo systemctl show ocpp-csms.service -p MainPID --value)" = "$before_pid"
test "$(sudo systemctl show ocpp-csms.service -p ExecStart --value)" = "$before_unit"
test "$(stat -c '%d:%i' "$db")" = "$before_inode"
sudo systemctl is-active --quiet ocpp-csms.service
/usr/local/bin/ocpp-csms status >/dev/null
echo "PASS: injected APT failure before cutover; current, PID, unit, DB inode and health preserved"
