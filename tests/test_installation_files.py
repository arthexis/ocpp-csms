from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_install_script_uses_staged_runtime_before_cutover():
    script = read("install.sh")
    assert 'STAGE_VENV="$PREFIX/venv.next"' in script
    assert 'PREVIOUS_VENV="$PREFIX/venv.previous"' in script
    assert 'render_service "$STAGE_VENV/bin/ocpp-csms"' in script
    assert 'systemd-analyze verify "$TMP_SERVICE"' in script
    assert 'render_service "$VENV/bin/ocpp-csms"' in script
    assert script.index('render_service "$STAGE_VENV/bin/ocpp-csms"') < script.index('systemd-analyze verify "$TMP_SERVICE"')
    assert script.index('systemd-analyze verify "$TMP_SERVICE"') < script.index('run_preflight --json')
    assert script.index('mv "$STAGE_VENV" "$VENV"') < script.index('render_service "$VENV/bin/ocpp-csms"')


def test_install_script_preserves_previous_runtime_for_rollback():
    script = read("install.sh")
    assert 'PREVIOUS_VENV="$PREFIX/venv.previous"' in script
    assert 'mv "$VENV" "$PREVIOUS_VENV"' in script
    assert 'mv "$PREVIOUS_VENV" "$VENV"' in script


def test_install_script_supports_discover_modes():
    script = read("install.sh")
    assert "--with-discover" in script
    assert "--without-discover" in script
    assert "DISCOVER_MODE=preserve" in script


def test_discover_script_has_explicit_install_and_uninstall_modes():
    script = read("discover.sh")
    assert "--install" in script
    assert "--uninstall" in script


def test_discover_service_uses_installed_package_and_owned_state():
    unit = read("systemd/ocpp-discover.service.in")
    assert "Description=OCPP Discover" in unit
    assert "After=network-online.target ocpp-csms.service" in unit
    assert "Type=simple" in unit
    assert "Type=oneshot" not in unit
    assert "RemainAfterExit" not in unit
    assert "User=" not in unit
    assert "WorkingDirectory=" not in unit
    assert 'ExecStart="@PYTHON@" -m ocpp_discover service' in unit
    assert "--runtime-dir /run/ocpp-discover" in unit
    assert "--persistent-dir /var/lib/ocpp-discover" in unit
    assert '--interface "@INTERFACE@"' in unit
    assert "--listen-port @PORT@" in unit
    assert "Restart=on-failure" in unit
    assert "RestartSec=5" in unit
    assert "WantedBy=multi-user.target" in unit


def test_discover_install_uses_debian_nftables_boot_loader_and_installed_package():
    script = read("discover.sh")
    assert 'INTERFACE=${OCPP_DISCOVER_INTERFACE:-eth0}' in script
    assert "sudo apt-get install -y tcpdump nftables iproute2" in script
    assert "Automatic dependency installation is supported only on Debian" in script
    assert "sudo systemctl enable nftables.service" in script
    assert "systemctl restart nftables" not in script
    assert 'sudo systemctl enable "$SERVICE_NAME"' in script
    assert 'sudo systemctl start --no-block "$SERVICE_NAME"' in script
    assert 'sudo systemctl disable "$SERVICE_NAME"' in script
    assert 'sudo "$PYTHON" -m ocpp_discover.lifecycle prepare' in script
    assert 'sudo "$PYTHON" -m ocpp_discover cleanup --state-dir /run/ocpp-discover' in script
    assert 'sudo "$PYTHON" -m ocpp_discover.lifecycle remove' in script
    assert 'set -- "$PYTHON" -m ocpp_discover run' in script
    assert 'sudo "$@"' in script
    assert 'cp "$ROOT/field/discover.py"' not in script
    assert 'cp "$ROOT/field/redirect.py"' not in script
    assert "DISCOVER_ROOT" not in script
    assert "ocpp-csms.service" not in script
