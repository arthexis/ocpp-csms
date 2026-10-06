from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_install_script_stages_candidate_before_safety_gate():
    script = read("install.sh")
    stage = script.index('render_service "$STAGE_VENV/bin/ocpp-csms"')
    verify = script.index('systemd-analyze verify "$TMP_SERVICE"')
    safety_gate = script.index('run_preflight --json')

    assert stage < verify < safety_gate


def test_install_script_preserves_previous_runtime_for_rollback():
    script = read("install.sh")
    preserve = script.index('mv "$VENV" "$PREVIOUS_VENV"')
    restore = script.index('mv "$PREVIOUS_VENV" "$VENV"')

    assert preserve < restore


def test_install_script_installs_discover_with_csms():
    script = read("install.sh")

    assert '"$STAGE_VENV/bin/ocpp-discover" --help' in script
    assert 'sudo "$VENV/bin/python" -m ocpp_discover.lifecycle prepare' in script
    assert 'sudo systemctl enable "$DISCOVER_SERVICE_NAME"' in script
    assert 'sudo systemctl is-active --quiet "$DISCOVER_SERVICE_NAME"' in script


def test_discover_service_runs_resident_package_with_owned_state():
    unit = read("systemd/ocpp-discover.service.in")

    assert 'ExecStart="@PYTHON@" -m ocpp_discover service' in unit
    assert "--runtime-dir /run/ocpp-discover" in unit
    assert "--persistent-dir /var/lib/ocpp-discover" in unit
    assert '--interface "@INTERFACE@"' in unit
    assert "--listen-port @PORT@" in unit
    assert "Restart=on-failure" in unit
    assert "WantedBy=multi-user.target" in unit
