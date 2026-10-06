from .helpers import DEFAULTS, TASKS, assert_task_order, read, task_section


def test_reconnect_probe_precedes_candidate_discover_recovery():
    reconnect = read(TASKS / "reconnect_with_discover.yml")

    assert_task_order(
        reconnect,
        "Probe charger reconnect before Discover recovery",
        "Inspect resident OCPP Discover service before recovery",
        "Stop resident OCPP Discover before candidate recovery",
        "Start candidate OCPP Discover recovery unit",
        "Verify charger reconnect after candidate Discover recovery",
        "Wait for candidate Discover ownership receipt",
        "Validate candidate Discover persistent state",
        "Require preserved candidate Discover persistent state",
    )


def test_candidate_discover_runs_from_immutable_candidate_release():
    reconnect = read(TASKS / "reconnect_with_discover.yml")
    start = task_section(reconnect, "Start candidate OCPP Discover recovery unit")

    assert "systemd-run" in start
    assert "{{ ocpp_csms_release_venv }}/bin/python" in start
    assert "- ocpp_discover" in start
    assert "- service" in start
    assert "{{ ocpp_discover_interface | default('eth0') }}" in start
    assert "{{ ocpp_csms_port | string }}" in start


def test_candidate_recovery_requires_durable_ownership_before_helper_stops():
    reconnect = read(TASKS / "reconnect_with_discover.yml")
    wait = task_section(reconnect, "Wait for candidate Discover ownership receipt")
    validate = task_section(reconnect, "Validate candidate Discover persistent state")
    require = task_section(reconnect, "Require preserved candidate Discover persistent state")

    assert "discovered.json" in wait
    assert "{{ ocpp_csms_discover_promotion_timeout }}" in wait
    assert "{{ ocpp_csms_reconnect_timeout }}" not in wait
    assert "ocpp_discover.lifecycle" in validate
    assert "- prepare" in validate
    assert ".state == 'preserved'" in require


def test_discover_promotion_timeout_is_separate_and_longer_than_reconnect_timeout():
    defaults = read(DEFAULTS)

    assert "ocpp_csms_reconnect_timeout: 30" in defaults
    assert "ocpp_csms_discover_promotion_timeout: 60" in defaults


def test_failed_candidate_discover_restores_previous_resident_service():
    reconnect = read(TASKS / "reconnect_with_discover.yml")
    restore = task_section(
        reconnect, "Restore previous resident OCPP Discover after failed recovery"
    )

    assert "ocpp_csms_discover_was_active.rc | default(1) == 0" in restore


def test_successful_candidate_recovery_keeps_old_resident_out_of_success_path():
    reconnect = read(TASKS / "reconnect_with_discover.yml")

    rescue = reconnect.index("\n  rescue:\n")
    restore = reconnect.index("Restore previous resident OCPP Discover after failed recovery")
    always = reconnect.index("\n  always:\n")
    assert rescue < restore < always
    assert "Stop candidate Discover recovery unit after verification" in reconnect[always:]
