#!/usr/bin/env bash
# Real Ansible convergence on a disposable GitHub runner, no chargers connected.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_ENVIRONMENT:-}" == github-hosted ]] || exit 2
manifest=/etc/gway-mcp-providers/ocpp.json
root="$HOME/.local/share/ocpp-csms"
pid="$(systemctl show ocpp-csms.service -p MainPID --value)"
policy=/etc/ocpp-csms-mcp-policy.json
test ! -e "$manifest"
test ! -e "$policy"
./deploy.sh --no-private-diag-probe -- -e ocpp_csms_mcp_enabled=true
test "$(stat -c '%u:%a' "$manifest")" = '0:644'
test "$(stat -c '%u:%a' "$(dirname "$manifest")")" = '0:755'
python3 - "$manifest" "$root" <<'PY'
import json, pathlib, subprocess, sys
p = pathlib.Path(sys.argv[1])
m = json.loads(p.read_text())
assert set(m) == {"source_id", "revision", "executable", "argv"}
assert m["source_id"] == "ocpp"
assert m["revision"] == subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
exe = pathlib.Path(sys.argv[2], "current").resolve() / "venv/bin/ocpp-csms-mcp"
assert m["executable"] == str(exe) and not exe.is_symlink()
assert m["argv"] == [str(exe), "--data-dir", m["argv"][2]]
unit = subprocess.check_output(["systemctl","show","ocpp-csms.service","-p","ExecStart","--value"], text=True)
assert "--data-dir " + m["argv"][2] in unit
frames = [{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26"}},
          {"jsonrpc":"2.0","method":"notifications/initialized"},
          {"jsonrpc":"2.0","id":2,"method":"tools/list"},
          {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"reset","arguments":{"charge_point_id":"CI","reset_type":"Soft"}}}]
proc = subprocess.run(m["argv"], input="".join(json.dumps(f)+"\n" for f in frames),
                      text=True, capture_output=True, check=True, timeout=10)
results = [json.loads(line)["result"] for line in proc.stdout.splitlines()]
assert results[0]["protocolVersion"] == "2025-03-26"
assert len(results[1]["tools"]) == 5
assert all(t["annotations"]["readOnlyHint"] is True for t in results[1]["tools"])
assert results[2]["isError"] and "execute_disabled" in str(results[2])
PY
test "$(stat -c '%u:%a' "$policy")" = '0:644'
policy_hash="$(sha256sum "$policy")"
before="$(stat -c '%i:%Y:%s' "$manifest")"
hash="$(sha256sum "$manifest")"
./deploy.sh --no-private-diag-probe
test "$(stat -c '%i:%Y:%s' "$manifest")" = "$before"
test "$(sha256sum "$manifest")" = "$hash"
test "$(systemctl show ocpp-csms.service -p MainPID --value)" = "$pid"
./deploy.sh --stage-only -- -e ocpp_csms_mcp_enabled=false
test "$(sha256sum "$manifest")" = "$hash"
test "$(sha256sum "$policy")" = "$policy_hash"
./deploy.sh --no-private-diag-probe -- -e ocpp_csms_mcp_enabled=false
test ! -e "$manifest"
./deploy.sh --no-private-diag-probe
test ! -e "$manifest"
./deploy.sh --no-private-diag-probe -- -e ocpp_csms_mcp_enabled=true
test "$(sha256sum "$manifest")" = "$hash"
echo "PASS: real installed read provider, fixed active storage, no command dispatch, idempotence, staging isolation"
# Leave the registration for the writer-boundary fault test immediately following.
