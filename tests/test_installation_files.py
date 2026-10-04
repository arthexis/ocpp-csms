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


def test_installer_exposes_short_csms_command_and_configures_user_path():
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert 'CSMS_COMMAND="$BIN_DIR/csms"' in installer
    assert 'ln -sf "$VENV/bin/ocpp-csms" "$CSMS_COMMAND"' in installer
    assert "ensure_user_bin_on_path" in installer
    assert 'bash) rc="$HOME/.bashrc"' in installer
    assert 'zsh) rc="$HOME/.zshrc"' in installer
    assert 'fish) rc="$HOME/.config/fish/config.fish"' in installer
    assert '*) rc="$HOME/.profile"' in installer
    assert 'path_line="export PATH=\\\"$BIN_DIR:\\$PATH\\\""' in installer
    assert 'path_line="fish_add_path \\\"$BIN_DIR\\\""' in installer
    assert "Open a new shell, or source your shell configuration" in installer
