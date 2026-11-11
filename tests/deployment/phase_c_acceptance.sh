#!/usr/bin/env bash
# Real C1/C2 stage-only tests, restricted to disposable GitHub-hosted runners.
set -euo pipefail
[[ "$GITHUB_ACTIONS" == true && "$RUNNER_ENVIRONMENT" == github-hosted ]] || exit 2
repo="$(git rev-parse --show-toplevel)"
owner="$HOME/.local/share/ocpp-csms"
before="$(readlink -f "$owner/current")"
pid="$(sudo systemctl show ocpp-csms.service -p MainPID --value)"
db="$HOME/ocpp-csms-data/schemas/8/ocpp-csms.sqlite3"
test -f "$db"
inode="$(stat -c '%d:%i' "$db")"
scratch="$(mktemp -d /tmp/csms-phase-c.XXXXXX)"
cleanup() {
  git -C "$repo" worktree remove --force "$scratch/staged" >/dev/null 2>&1 || true
  git -C "$repo" worktree remove --force "$scratch/incompatible" >/dev/null 2>&1 || true
  rm -rf "$scratch"
}
trap cleanup EXIT
git worktree add --detach "$scratch/staged" HEAD
printf '\nCI phase C alternative release\n' >>"$scratch/staged/README.md"
git -C "$scratch/staged" add README.md
git -C "$scratch/staged" -c user.name="CI Fixture" -c user.email="ci@example.invalid" commit -qm "fixture: alternate release"
(cd "$scratch/staged" && ./deploy.sh --stage-only)
id="$(git -C "$scratch/staged" rev-parse --short=12 HEAD)"
candidate="$owner/releases/$id"
python3 - "$before" "$candidate" <<'PY'
from pathlib import Path
import subprocess
import sys
old, new = map(Path, sys.argv[1:])
def site(release):
    python = release / "venv/bin/python"
    paths = subprocess.check_output([str(python), "-c",
        "import sysconfig; print(sysconfig.get_paths()['purelib'])"], text=True).strip()
    link = Path(paths) / "ocpp_csms_dependencies.pth"
    assert link.is_file(), f"missing .pth: {link}"
    location = Path(link.read_text().strip())
    assert location.is_dir(), f"invalid .pth target: {location}"
    return location
a, b = site(old), site(new)
assert a != b, "C1: staged release shares mutable dependency site with current"
def install(site, version):
    (site / "csms_ci_probe.py").write_text(f"VERSION = '{version}'\n")
    metadata = site / f"csms_ci_probe-{version}.dist-info"
    metadata.mkdir(exist_ok=True)
    (metadata / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: csms-ci-probe\nVersion: {version}\n")
install(a, "1.0")
install(b, "2.0")
for release, expected in ((old, "1.0"), (new, "2.0"), (old, "1.0")):
    actual = subprocess.check_output([str(release / "venv/bin/python"), "-c",
        "import csms_ci_probe; print(csms_ci_probe.VERSION)"], text=True).strip()
    assert actual == expected, f"C1: {release} imported {actual}, expected {expected}"
print("PASS C1: independent resolved distribution versions including stage-only")
PY
git worktree add --detach "$scratch/incompatible" HEAD
python3 - "$scratch/incompatible/pyproject.toml" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); s=p.read_text()
assert s.count('"websockets>=12,<14"') == 1
p.write_text(s.replace('"websockets>=12,<14"', '"websockets>=999,<1000"'))
PY
git -C "$scratch/incompatible" add pyproject.toml
git -C "$scratch/incompatible" -c user.name="CI Fixture" -c user.email="ci@example.invalid" commit -qm "fixture: incompatible wheel"
if (cd "$scratch/incompatible" && ./deploy.sh --stage-only) >"$scratch/stage.log" 2>&1; then
  echo "::error::C2: incompatible wheel accepted during staging" >&2
  exit 1
fi
grep -i websockets "$scratch/stage.log" >/dev/null || {
  cat "$scratch/stage.log"; echo "::error::C2 failed for unrelated reason" >&2; exit 1;
}
test "$(readlink -f "$owner/current")" = "$before"
test "$(sudo systemctl show ocpp-csms.service -p MainPID --value)" = "$pid"
test "$(stat -c '%d:%i' "$db")" = "$inode"
sudo systemctl is-active --quiet ocpp-csms.service
echo "PASS C2: incompatible wheel fails closed without changing current or SQLite"
