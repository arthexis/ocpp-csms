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


def test_base_installer_help_explains_safe_handoff_and_optional_discovery():
    help_output = help_text("install.sh")
    assert "--host HOST" in help_output
    assert "--port PORT" in help_output
    assert "--rollover" not in help_output
    assert "Idle connected chargers are expected" in help_output
    assert "Active charging always blocks installation" in help_output
    assert "safe staged handoff" in help_output
    assert "--with-discover" in help_output
    assert "--without-discover" in help_output
    assert "preserved" in help_output


def test_base_installer_stages_before_disruptive_cutover():
    installer = read("install.sh")
    first_preflight = installer.index("# First safety gate")
    stage_venv = installer.index('python3 -m venv "$STAGE_VENV"')
    schema_check = installer.index("schema-check")
    final_preflight = installer.index("# Second safety gate")
    stop_service = installer.index('sudo systemctl stop "$SERVICE_NAME"')
    schema_upgrade = installer.index("schema-upgrade")
    promote = installer.index('mv "$STAGE_VENV" "$VENV"')
    install_service = installer.index('sudo install -m 0644 "$TMP_SERVICE" "$SERVICE_PATH"')
    reconnect = installer.index("wait-reconnect")

    assert first_preflight < stage_venv < schema_check < final_preflight
    assert final_preflight < stop_service < schema_upgrade < promote < install_service < reconnect
    assert 'STAGE_VENV="$PREFIX/venv.next"' in installer
    assert 'PREVIOUS_VENV="$PREFIX/venv.previous"' in installer


def test_base_installer_keeps_active_charge_gate_and_conditional_rollback():
    installer = read("install.sh")
    assert "run_preflight --json" in installer
    assert "--rollover" not in installer
    assert 'if [ "$SCHEMA_ACTION" != upgrade ]' in installer
    assert "automatic rollback is intentionally disabled" in installer
    assert 'mv "$PREVIOUS_VENV" "$VENV"' in installer
    assert 'mv "$VENV" "$VENV.failed"' in installer


def test_base_installer_preserves_and_delegates_discovery_after_cutover():
    installer = read("install.sh")
    reconnect = installer.index("wait-reconnect")
    discover = installer.index('sh "$ROOT/discover.sh" --install')
    assert 'DISCOVER_MODE=preserve' in installer
    assert 'DISCOVER_MODE=install' in installer
    assert 'DISCOVER_MODE=uninstall' in installer
    assert reconnect < discover
    assert 'sh "$ROOT/discover.sh" --uninstall' in installer
    assert 'ocpp_csms.install_preflight --data-dir "$DATA_DIR" >/dev/null' in installer


def test_base_installer_exposes_csms_and_ocpp_csms_commands_and_configures_user_path():
    installer = read("install.sh")
    assert 'COMMAND="$BIN_DIR/ocpp-csms"' in installer
    assert 'CSMS_COMMAND="$BIN_DIR/csms"' in installer
    assert 'ln -sf "$VENV/bin/ocpp-csms" "$COMMAND"' in installer
    assert 'ln -sf "$VENV/bin/ocpp-csms" "$CSMS_COMMAND"' in installer
    assert "ensure_user_bin_on_path" in installer
    assert 'bash) rc="$HOME/.bashrc"' in installer
    assert 'zsh) rc="$HOME/.zshrc"' in installer
    assert 'fish) rc="$HOME/.config/fish/config.fish"' in installer
    assert '*) rc="$HOME/.profile"' in installer


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
    assert 'set -- "$PYTHON" -m field.discover run' in script
    assert 'sudo "$@"' in script
    assert 'cp "$ROOT/field/discover.py" "$DISCOVER_ROOT/field/discover.py"' in script
    assert 'cp "$ROOT/field/redirect.py" "$DISCOVER_ROOT/field/redirect.py"' in script
    assert "ocpp-csms.service" not in script
