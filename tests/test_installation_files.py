from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_systemd_service_runs_as_installing_user_and_restarts():
    unit = (ROOT / "systemd" / "ocpp-csms.service.in").read_text(encoding="utf-8")

    assert "User=@USER@" in unit
    assert "Group=@GROUP@" in unit
    assert 'Environment="HOME=@HOME@"' in unit
    assert 'ExecStart="@COMMAND@" serve --data-dir "@DATA_DIR@"' in unit
    assert "Restart=always" in unit
    assert "WantedBy=multi-user.target" in unit


def test_installer_enables_and_validates_service():
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert 'sudo systemctl enable --now "$SERVICE_NAME"' in installer
    assert 'sudo systemctl is-active --quiet "$SERVICE_NAME"' in installer
    assert '"$COMMAND" --data-dir "$DATA_DIR" status >/dev/null' in installer
    assert 'sudo journalctl -u "$SERVICE_NAME" -n 20 --no-pager' in installer
