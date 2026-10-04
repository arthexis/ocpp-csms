from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_systemd_service_runs_as_installing_user_and_restarts():
    unit = (ROOT / "systemd" / "ocpp-csms.service.in").read_text(encoding="utf-8")

    assert "User=@USER@" in unit
    assert "Group=@GROUP@" in unit
    assert 'Environment="HOME=@HOME@"' in unit
    assert 'ExecStart="@COMMAND@" --data-dir "@DATA_DIR@" serve' in unit
    assert 'serve --host "@HOST@" --port @PORT@' in unit
    assert "Restart=always" in unit
    assert "WantedBy=multi-user.target" in unit


def test_installer_configures_enables_and_validates_service():
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert 'if [ "$(id -u)" -eq 0 ]' in installer
    assert "Do not run this installer as root or with sudo" in installer
    assert 'HOST=${OCPP_CSMS_HOST:-"0.0.0.0"}' in installer
    assert 'PORT=${OCPP_CSMS_PORT:-9000}' in installer
    assert "--host)" in installer
    assert "--port)" in installer
    assert '"$VENV/bin/python" - "$HOST" "$PORT"' in installer
    assert 'sudo systemctl enable --now "$SERVICE_NAME"' in installer
    assert 'sudo systemctl is-active --quiet "$SERVICE_NAME"' in installer
    assert '"$COMMAND" --data-dir "$DATA_DIR" status >/dev/null' in installer
    assert 'probe_host = "127.0.0.1" if host == "0.0.0.0" else "::1" if host == "::" else host' in installer
    assert 'socket.create_connection((probe_host, port), timeout=0.5)' in installer
    assert 'sudo journalctl -u "$SERVICE_NAME" -n 20 --no-pager' in installer


def test_base_installer_explains_and_delegates_optional_discovery():
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert "--with-discover" in installer
    assert "--without-discover" in installer
    assert "Also install and enable OCPP Discover" in installer
    assert "existing OCPP Discover enabled/disabled state is preserved" in installer
    assert 'DISCOVER_MODE=preserve' in installer
    assert 'sh "$ROOT/discover.sh" --install' in installer
    assert 'sh "$ROOT/discover.sh" --uninstall' in installer


def test_discover_service_is_root_boot_helper_that_retries_only_failures():
    unit = (ROOT / "systemd" / "ocpp-discover.service.in").read_text(encoding="utf-8")

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


def test_discover_wrapper_keeps_install_and_runtime_behavior_separate():
    script = (ROOT / "discover.sh").read_text(encoding="utf-8")

    assert "--install" in script
    assert "--uninstall" in script
    assert "--cleanup" in script
    assert 'INTERFACE=${OCPP_DISCOVER_INTERFACE:-eth0}' in script
    assert "sudo apt-get install -y tcpdump nftables iproute2" in script
    assert "Automatic dependency installation is supported only on Debian" in script
    assert "show_dependency_help" in script
    assert 'sudo systemctl enable "$SERVICE_NAME"' in script
    assert 'sudo systemctl start --no-block "$SERVICE_NAME"' in script
    assert 'sudo systemctl disable "$SERVICE_NAME"' in script
    assert 'sudo "$PYTHON" -m field.discover cleanup' in script
    assert 'sudo "$PYTHON" -m field.discover run' in script
    assert 'cp "$ROOT/field/discover.py" "$DISCOVER_ROOT/field/discover.py"' in script
    assert 'cp "$ROOT/field/redirect.py" "$DISCOVER_ROOT/field/redirect.py"' in script
