from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def help_text(script: str) -> str:
    result = subprocess.run(
        ["sh", str(ROOT / script), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_base_service_runs_unprivileged_and_restarts():
    unit = read("systemd/ocpp-csms.service.in")

    assert "User=@USER@" in unit
    assert "Group=@GROUP@" in unit
    assert 'Environment="HOME=@HOME@"' in unit
    assert 'ExecStart="@COMMAND@" --data-dir "@DATA_DIR@" serve --host "@HOST@" --port @PORT@' in unit
    assert "Restart=always" in unit
    assert "WantedBy=multi-user.target" in unit


def test_base_installer_help_explains_optional_discovery():
    help_output = help_text("install.sh")

    assert "--host HOST" in help_output
    assert "--port PORT" in help_output
    assert "--with-discover" in help_output
    assert "Also install and enable OCPP Discover" in help_output
    assert "--without-discover" in help_output
    assert "preserved" in help_output


def test_base_installer_preserves_and_delegates_discovery_state():
    installer = read("install.sh")

    assert 'DISCOVER_MODE=preserve' in installer
    assert 'DISCOVER_MODE=install' in installer
    assert 'DISCOVER_MODE=uninstall' in installer
    assert 'sh "$ROOT/discover.sh" --install' in installer
    assert 'sh "$ROOT/discover.sh" --uninstall' in installer
    assert 'sudo systemctl enable --now "$SERVICE_NAME"' in installer
    assert '"$COMMAND" --data-dir "$DATA_DIR" status >/dev/null' in installer


def test_discover_help_exposes_runtime_and_install_surfaces():
    help_output = help_text("discover.sh")

    assert "Run OCPP network discovery immediately by default" in help_output
    assert "--interface IFACE" in help_output
    assert "--install" in help_output
    assert "--uninstall" in help_output
    assert "--cleanup" in help_output


def test_discover_service_is_root_boot_helper_that_retries_only_failures():
    unit = read("systemd/ocpp-discover.service.in")

    assert "Description=OCPP Discover" in unit
    assert "After=network-online.target ocpp-csms.service" in unit
    assert "Type=oneshot" in unit
    assert "User=" not in unit
    assert 'WorkingDirectory="@DISCOVER_ROOT@"' in unit
    assert 'ExecStart="@PYTHON@" -m field.discover run' in unit
    assert "--state-dir /run/ocpp-discover" in unit
    assert '--interface "@INTERFACE@"' in unit
    assert "--listen-port @PORT@" in unit
    assert "Restart=on-failure" in unit
    assert "RestartSec=5" in unit
    assert "WantedBy=multi-user.target" in unit


def test_discover_install_is_debian_scoped_and_keeps_base_service_separate():
    script = read("discover.sh")

    assert 'INTERFACE=${OCPP_DISCOVER_INTERFACE:-eth0}' in script
    assert "sudo apt-get install -y tcpdump nftables iproute2" in script
    assert "Automatic dependency installation is supported only on Debian" in script
    assert 'sudo systemctl enable "$SERVICE_NAME"' in script
    assert 'sudo systemctl start --no-block "$SERVICE_NAME"' in script
    assert 'sudo systemctl disable "$SERVICE_NAME"' in script
    assert 'sudo "$PYTHON" -m field.discover cleanup' in script
    assert 'sudo "$PYTHON" -m field.discover run' in script
    assert 'cp "$ROOT/field/discover.py" "$DISCOVER_ROOT/field/discover.py"' in script
    assert 'cp "$ROOT/field/redirect.py" "$DISCOVER_ROOT/field/redirect.py"' in script
    assert "ocpp-csms.service" not in script
