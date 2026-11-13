#!/usr/bin/env bash
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_ENVIRONMENT:-}" == github-hosted ]] || exit 2
root="$(git rev-parse --show-toplevel)"
fixture="$(mktemp -d /tmp/ocpp-csms-apt-retry.XXXXXX)"
log="$(mktemp)"
trap 'rm -f "$log"; git -C "$root" worktree remove --force "$fixture" 2>/dev/null || true' EXIT
git worktree add --detach "$fixture" HEAD
# Exercise the actual playbook retry/until/delay policy, substituting ONLY
# the APT action with a deterministic failure/failure/success command.
python3 - "$fixture/ansible/playbooks/install.yml" "$fixture" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
root = Path(sys.argv[2])
source = path.read_text()
start = source.index('      ansible.builtin.apt:', source.index('Preflight all appliance runtime packages'))
end = source.index('      register: ocpp_apt_preflight', start)
probe = root / 'apt_retry_probe.py'
probe.write_text("""from pathlib import Path
import sys
p = Path(__file__).with_suffix('.count')
n = int(p.read_text()) + 1 if p.exists() else 1
p.write_text(str(n))
if n < 3:
    print('APT_REFRESH_TRANSIENT attempt=' + str(n), file=sys.stderr)
    sys.exit(42)
print('APT_REFRESH_RECOVERED attempt=' + str(n))
""")
replacement = '      ansible.builtin.command:\\n        argv:\\n          - python3\\n          - ' + str(probe) + '\\n      changed_when: false\\n'
replacement = replacement.replace('\\n', '\n')
path.write_text(source[:start] + replacement + source[end:])
PY
before_release="$(readlink -f "$HOME/.local/share/ocpp-csms/current")"
before_pid="$(sudo systemctl show ocpp-csms.service -p MainPID --value)"
before_unit="$(sudo systemctl show ocpp-csms.service -p ExecStart --value)"
# The retry fixture exercises the real Ansible until/retries policy without
# changing host APT sources, cache, packages, or service configuration.
if ! (cd "$fixture" && ./deploy.sh -- -vvv) >"$log" 2>&1; then
  cat "$log"; echo "::error::APT retry fixture failed" >&2; exit 1
fi
test "$(cat "$fixture/apt_retry_probe.count")" = 3
grep -F 'APT_REFRESH_TRANSIENT attempt=1' "$log" >/dev/null || { tail -n 100 "$log"; exit 1; }
grep -F 'APT_REFRESH_TRANSIENT attempt=2' "$log" >/dev/null || { tail -n 100 "$log"; exit 1; }
grep -F 'APT_REFRESH_RECOVERED attempt=3' "$log" >/dev/null || { tail -n 100 "$log"; exit 1; }
test "$(readlink -f "$HOME/.local/share/ocpp-csms/current")" = "$before_release"
test "$(sudo systemctl show ocpp-csms.service -p MainPID --value)" = "$before_pid"
test "$(sudo systemctl show ocpp-csms.service -p ExecStart --value)" = "$before_unit"
sudo systemctl is-active --quiet ocpp-csms.service
echo "PASS: real Ansible retry policy recovered after two simulated APT failures; receipt and service preserved"
