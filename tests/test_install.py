from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_installer_exposes_listener_configuration():
    script = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert 'HOST=${OCPP_CSMS_HOST:-"0.0.0.0"}' in script
    assert 'PORT=${OCPP_CSMS_PORT:-9000}' in script
    assert "--host)" in script
    assert "--port)" in script
    assert '"$VENV/bin/python" - "$HOST" "$PORT"' in script
    assert 'socket.create_connection((probe_host, port)' in script


def test_systemd_service_uses_installed_listener_endpoint():
    template = (ROOT / "systemd" / "ocpp-csms.service.in").read_text(encoding="utf-8")

    assert 'serve --host "@HOST@" --port @PORT@' in template
