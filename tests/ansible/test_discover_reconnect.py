from .helpers import DEFAULTS, TASKS, load_yaml, task_by_name


RECONNECT_TASKS = TASKS / "reconnect_with_discover.yml"


def test_candidate_discover_runs_from_immutable_candidate_release():
    start = task_by_name(RECONNECT_TASKS, "Start candidate OCPP Discover recovery unit")
    argv = start["ansible.builtin.command"]["argv"]

    assert argv[0] == "systemd-run"
    assert "{{ ocpp_csms_release_venv }}/bin/python" in argv
    assert "ocpp_discover" in argv
    assert "service" in argv


def test_candidate_recovery_requires_durable_ownership_before_helper_stops():
    wait = task_by_name(RECONNECT_TASKS, "Wait for candidate Discover ownership receipt")
    validate = task_by_name(RECONNECT_TASKS, "Validate candidate Discover persistent state")
    require = task_by_name(RECONNECT_TASKS, "Require preserved candidate Discover persistent state")

    wait_for = wait["ansible.builtin.wait_for"]
    assert wait_for["path"].endswith("/discovered.json")
    assert wait_for["timeout"] == "{{ ocpp_csms_discover_promotion_timeout }}"

    argv = validate["ansible.builtin.command"]["argv"]
    assert argv[2:4] == ["ocpp_discover.lifecycle", "prepare"]
    assert require["ansible.builtin.assert"]["that"]


def test_discover_promotion_timeout_is_longer_than_reconnect_timeout():
    defaults = load_yaml(DEFAULTS)
    assert defaults["ocpp_csms_discover_promotion_timeout"] > defaults["ocpp_csms_reconnect_timeout"]


def test_failed_candidate_discover_restores_previous_resident_service():
    restore = task_by_name(
        RECONNECT_TASKS, "Restore previous resident OCPP Discover after failed recovery"
    )
    assert restore["when"] == "ocpp_csms_discover_was_active.rc | default(1) == 0"


def test_candidate_helper_is_always_stopped_after_recovery_attempt():
    recover = task_by_name(RECONNECT_TASKS, "Recover charger reconnect with candidate OCPP Discover")
    always_names = [task["name"] for task in recover["always"]]
    assert "Stop candidate Discover recovery unit after verification" in always_names
