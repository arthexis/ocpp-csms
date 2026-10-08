"""Deployment safety contracts for optional WSS provisioning.

These checks make TLS provisioning stay idempotent and non-destructive.
The Ansible CI separately validates the role syntax.
"""
from pathlib import Path

ROLE = Path(__file__).resolve().parents[2] / "ansible/roles/ocpp_csms/tasks/main.yml"


def test_tls_provisioning_does_not_overwrite_credential_content():
    source = ROLE.read_text(encoding="utf-8")
    section = source.split("- name: Prepare persistent OCPP CSMS TLS directories", 1)[1]
    section = section.split("- name: Render candidate OCPP CSMS systemd unit", 1)[0]
    assert "state: directory" in section
    assert 'path: "/etc/ocpp-csms/tls"' in section
    assert 'mode: "0700"' in section
    assert "state: file" in section
    assert 'mode: "0600"' in section
    assert "content:" not in section
    assert "src:" not in section


def test_tls_enabled_preflight_runs_as_non_root_service_user():
    source = ROLE.read_text(encoding="utf-8")
    section = source.split("- name: Check deployed TLS readiness as service account", 1)[1]
    section = section.split("- name: Render candidate OCPP CSMS systemd unit", 1)[0]
    assert "ocpp_csms_release_command" in section
    assert "ocpp_csms_port" in section
    assert "ocpp_csms_tls_enablement.stdout == 'enabled'" in section
    assert "ocpp_csms_tls_readiness.rc == 0" in section
    assert "become: true" not in section.split("- name: Read TLS configuration enablement", 1)[0]
