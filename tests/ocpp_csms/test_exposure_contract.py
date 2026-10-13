"""Deployment-contract tests for public nginx WSS exposure."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "ansible/roles/ocpp_expose/templates/ocpp-csms-site.conf.j2"
TASKS = ROOT / "ansible/roles/ocpp_expose/tasks/main.yml"
CLOUD = ROOT / "ansible/playbooks/delegate.yml"


def test_wss_site_forwards_auth_upgrade_and_ocpp_subprotocol():
    content = SITE.read_text()
    assert "listen 443 ssl;" in content
    assert "listen 80;" in content
    assert "server_name {{ ocpp_csms_expose }}" in content
    assert 'if ($host != "{{ ocpp_csms_expose }}")' in content
    assert "proxy_pass http://127.0.0.1:{{ ocpp_csms_port }}" in content
    assert "proxy_set_header Authorization $http_authorization;" in content
    assert "proxy_set_header Upgrade $http_upgrade;" in content
    assert "proxy_set_header Sec-WebSocket-Protocol $http_sec_websocket_protocol;" in content
    assert "proxy_http_version 1.1;" in content
    assert "proxy_read_timeout 3600s;" in content


def test_nginx_acme_renewal_and_no_plaintext_ocpp():
    content = SITE.read_text()
    http, tls = content.split("# Restrict attempted WebSocket handshakes", 1)
    assert "/.well-known/acme-challenge/" in http
    assert "proxy_pass" not in http
    assert "location / { return 404; }" in http
    assert "limit_req_zone" in tls
    assert "limit_req zone=ocpp_wss_handshake burst=20 nodelay;" in tls
    tasks = TASKS.read_text()
    assert "certbot.timer" in tasks
    assert "ocpp-csms-renew-hook.sh.j2" in tasks


def test_exposure_rollback_and_auth_gate():
    tasks = TASKS.read_text()
    assert "Snapshot existing nginx site before promotion" in tasks
    assert "Promote validated public WSS site with rollback" in tasks
    assert "rescue:" in tasks
    assert "Restore previous nginx site" in tasks
    assert "Disable unvalidated site when there was no predecessor" in tasks
    assert "nginx -t" in tasks
    assert "mode == 'basic'" in tasks
    cloud = CLOUD.read_text()
    assert "Verify Basic auth is configured before cloud deployment" in cloud


def test_explicit_satellite_flag_is_default_and_conflicts_with_other_modes():
    shell = (ROOT / "deploy.sh").read_text()
    assert 'PLAYBOOK="$ROOT/ansible/playbooks/satellite.yml"' in shell
    assert '        --satellite)' in shell
    assert 'satellite=1' in shell
    assert 'if [ "$satellite" -eq 1 ] && { [ "$delegate" -eq 1 ] || [ -n "$expose" ]; }; then' in shell
    assert '--satellite cannot be combined with --delegate or --expose.' in shell
