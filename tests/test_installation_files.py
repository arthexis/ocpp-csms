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


def test_install_script_always_installs_discover_with_csms():
    script = read("install.sh")
    assert "--with-discover" not in script
    assert "--without-discover" not in script
    assert "DISCOVER_MODE" not in script
    assert 'DISCOVER_COMMAND="$BIN_DIR/ocpp-discover"' in script
    assert '"$STAGE_VENV/bin/ocpp-discover" --help' in script
    assert 'ln -sf "$VENV/bin/ocpp-discover" "$DISCOVER_COMMAND"' in script
    assert 'sudo "$VENV/bin/python" -m ocpp_discover.lifecycle prepare' in script
    assert 'sudo systemctl enable "$DISCOVER_SERVICE_NAME"' in script
    assert 'sudo systemctl is-active --quiet "$DISCOVER_SERVICE_NAME"' in script


def test_install_script_stages_discover_before_final_safety_gate():
    script = read("install.sh")
    assert 'render_discover_service "$STAGE_VENV/bin/python"' in script
    assert 'systemd-analyze verify "$TMP_DISCOVER_SERVICE"' in script
    assert 'install_discover_dependencies' in script
    assert script.index('render_discover_service "$STAGE_VENV/bin/python"') < script.index('run_preflight --json')
    assert script.index('systemd-analyze verify "$TMP_DISCOVER_SERVICE"') < script.index('run_preflight --json')
    assert script.index('install_discover_dependencies') < script.index('run_preflight --json')


def test_install_script_does_not_rollback_healthy_csms_for_discover_failure():
    script = read("install.sh")
    discover_convergence = script.index('# Discover is part of the appliance.')
    assert script.index('"$COMMAND" --data-dir "$DATA_DIR" status >/dev/null') < discover_convergence
    tail = script[discover_convergence:]
    assert "rollback_startup" not in tail
    assert "CSMS installation succeeded, but OCPP Discover" in tail


def test_discover_script_is_compatibility_only_not_an_installer():
    script = read("discover.sh")
    assert "Installation and service ownership belong to `install.sh` or Ansible." in script
    assert "install_discovery()" not in script
    assert "uninstall_discovery()" not in script
    assert "apt-get install" not in script
    assert "systemctl enable" not in script
    assert "ocpp_discover.lifecycle prepare" not in script
    assert "ocpp_discover.lifecycle remove" not in script
    assert "--install` and `--uninstall` are no longer supported" in script


def test_discover_script_delegates_operator_commands_to_installed_cli():
    script = read("discover.sh")
    assert "command -v ocpp-discover" in script
    assert 'exec "$DISCOVER" status' in script
    assert 'exec "$DISCOVER" diagnostics' in script
    assert 'exec sudo "$DISCOVER" cleanup --state-dir /run/ocpp-discover' in script
    assert 'set -- "$DISCOVER" run' in script
    assert 'exec sudo "$@"' in script


def test_discover_script_preserves_explicit_manual_discovery_options():
    script = read("discover.sh")
    assert 'INTERFACE=${OCPP_DISCOVER_INTERFACE:-eth0}' in script
    assert "--existing-endpoint-only" in script
    assert "--diagnostic-only" in script
    assert "--passive-capture-log" in script
    assert "--grace-seconds" in script
    assert "--arp-seconds" in script
    assert "--tcp-seconds" in script


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
