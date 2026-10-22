"""Contract tests for independent CSMS installation and nginx WSS exposure."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "ansible/roles/ocpp_expose/templates/ocpp-csms-site.conf.j2"
TASKS = ROOT / "ansible/roles/ocpp_expose/tasks/main.yml"


def test_wss_site_forwards_auth_upgrade_and_ocpp_subprotocol():
    content = SITE.read_text()
    assert "listen 443 ssl;" in content
    assert "listen 80;" in content
    assert "server_name {{ ocpp_csms_expose }}" in content
    assert 'if ($host != "{{ ocpp_csms_expose }}")' in content
    assert "proxy_pass http://127.0.0.1:{{ ocpp_expose_upstream_port }}" in content
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
    zone = "ocpp_wss_{{ ocpp_csms_expose | replace('.', '_') | replace('-', '_') }}"
    assert f"zone={zone}:10m rate=2r/s;" in tls
    assert f"limit_req zone={zone} burst=20 nodelay;" in tls
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


def test_install_expose_and_uninstall_are_independent():
    shell = (ROOT / "deploy.sh").read_text()
    assert 'playbook="$ROOT/ansible/playbooks/install.yml"' in shell
    assert 'playbook="$ROOT/ansible/playbooks/expose.yml"' in shell
    assert 'exec ansible-playbook "$ROOT/ansible/playbooks/uninstall.yml"' in shell
    assert (ROOT / "ansible/playbooks/install.yml").exists()
    assert (ROOT / "ansible/playbooks/expose.yml").exists()
    assert (ROOT / "ansible/playbooks/uninstall.yml").exists()


def test_nginx_dav_regex_quantifiers_are_quoted():
    """All upload location regexes must quote nginx-special PCRE quantifiers."""
    content = SITE.read_text()
    locations = [line.strip() for line in content.splitlines() if "location ~ " in line and "/ocpp-diagnose/" in line]
    assert len(locations) == 3
    assert all(line.startswith('location ~ "^/ocpp-diagnose/') for line in locations)
    assert all("{40}" in line for line in locations)
    assert all(line.endswith('$" {') for line in locations)
    assert sum("{0,99}" in line for line in locations) == 1
    assert sum("upload" in line and "{0,99}" not in line for line in locations) == 2
