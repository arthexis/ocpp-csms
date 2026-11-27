"""Guard Reports' separate runtime consumer identity at CSMS release handoff."""
from pathlib import Path

ROLE = Path(__file__).resolve().parents[2] / "ansible/roles/ocpp_csms/tasks/main.yml"


def _contract():
    source = ROLE.read_text(encoding="utf-8")
    before, section = source.split("- name: Detect existing Reports ingestion identity", 1)
    section, after = section.split("- name: Resolve OCPP CSMS service group", 1)
    return before, section, after


def test_reports_runtime_gate_precedes_staging_and_writer_handoff():
    before, section, after = _contract()
    assert "Verify release wheel dependency compatibility" in before
    assert "Verify immutable release command" in before
    assert "End host after safe staging" in after
    assert "Perform controlled OCPP CSMS handoff" in after
    assert "ocpp_csms_release_ready" not in section
    assert "ocpp_csms_reports_account.rc == 0" in section
    assert "ocpp_csms_reports_account.rc not in [0, 2]" in section


def test_access_repair_is_conditional_and_scoped_to_candidate_runtime():
    _, section, _ = _contract()
    assert "ocpp_csms_reports_runtime_probe.rc | default(1) != 0" in section
    assert 'argv: [setfacl, -R, -P, -m, "u:ocpp-reports:rX", "{{ item }}"]' in section
    assert 'argv: [setfacl, -m, "u:ocpp-reports:--x", "{{ item }}"]' in section
    assert section.count('ocpp_csms_dependency_venv') == 1
    assert "ocpp_csms_build_venv" not in section
    assert "ocpp_csms_data_dir" not in section
    assert "ansible_env.HOME" not in section
    assert "/etc/ocpp-csms" not in section
    assert "chmod" not in section


def test_probes_use_consumer_identity_and_fail_closed_after_repair():
    _, section, _ = _contract()
    before, final = section.split("- name: Require candidate runtime and dependency imports as Reports", 1)
    for probe in (before, final):
        assert "become_user: ocpp-reports" in probe
        assert "import ocpp_csms, websockets" in probe
        assert "'--help'], check=True" in probe
        assert 'PYTHONDONTWRITEBYTECODE: "1"' in probe
    assert "failed_when: false" in before
    assert "failed_when: false" not in final
