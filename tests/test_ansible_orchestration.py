from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = ROOT / "ansible"
ROLE_TASKS = ANSIBLE / "roles" / "ocpp_csms" / "tasks"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def task_position(text: str, name: str) -> int:
    marker = f"- name: {name}"
    assert marker in text, f"missing Ansible task: {name}"
    return text.index(marker)


def test_ansible_has_two_safety_gate_boundaries():
    main = read(ROLE_TASKS / "main.yml")

    local_initial = task_position(main, "Run local initial install safety preflight")
    remote_initial = task_position(main, "Run remote initial install safety preflight")
    staging = task_position(main, "Install Python prerequisites")
    final = task_position(main, "Run final install safety preflight")
    cutover = task_position(main, "Perform controlled OCPP CSMS handoff")

    assert local_initial < staging
    assert remote_initial < staging
    assert staging < final < cutover


def test_ansible_has_no_retired_rollover_surface():
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in ANSIBLE.rglob("*")
        if path.is_file()
    )

    assert "rollover" not in text.lower()


def test_handoff_baselines_connections_before_stopping_service():
    cutover = read(ROLE_TASKS / "cutover.yml")

    baseline = task_position(cutover, "Capture charger reconnect baseline")
    stop = task_position(cutover, "Stop existing OCPP CSMS service")

    assert baseline < stop


def test_schema_upgrade_can_only_happen_after_service_stop():
    cutover = read(ROLE_TASKS / "cutover.yml")

    stop = task_position(cutover, "Stop existing OCPP CSMS service")
    upgrade = task_position(cutover, "Upgrade database schema after service stop")
    activate = task_position(cutover, "Activate immutable release")

    assert stop < upgrade < activate


def test_activation_is_verified_before_reconnect_success():
    cutover = read(ROLE_TASKS / "cutover.yml")

    start = task_position(cutover, "Enable and start activated OCPP CSMS service")
    listener = task_position(cutover, "Wait for OCPP CSMS listener")
    status = task_position(cutover, "Verify OCPP CSMS application status")
    reconnect = task_position(cutover, "Verify previously connected chargers reconnect")

    assert start < listener < status < reconnect


def test_rollback_is_forbidden_after_schema_upgrade():
    cutover = read(ROLE_TASKS / "cutover.yml")

    assert cutover.count("ocpp_csms_schema_action != 'upgrade'") >= 5
    assert "ocpp_csms_schema_action == 'upgrade'" in cutover
    assert "Automatic rollback was not" in cutover
    assert "the database schema was upgraded" in cutover


def test_converged_run_does_not_enter_handoff_block():
    cutover = read(ROLE_TASKS / "cutover.yml")

    handoff = task_position(cutover, "Activate and verify OCPP CSMS handoff")
    converged = task_position(cutover, "Ensure converged OCPP CSMS service is enabled and running")

    assert handoff < converged
    assert "when: ocpp_csms_activation_required | bool" in cutover
    assert cutover.count("when: not (ocpp_csms_activation_required | bool)") >= 3


def test_base_role_validates_candidate_and_initialized_storage():
    main = read(ROLE_TASKS / "main.yml")
    cutover = read(ROLE_TASKS / "cutover.yml")

    candidate_verify = task_position(main, "Verify candidate systemd unit before handoff")
    final_preflight = task_position(main, "Run final install safety preflight")
    assert candidate_verify < final_preflight
    assert "systemd-analyze" in main

    init = task_position(cutover, "Initialize OCPP CSMS storage with active release")
    database = task_position(cutover, "Inspect initialized OCPP CSMS database")
    transactions = task_position(cutover, "Inspect initialized transaction archive")
    writable = task_position(cutover, "Verify OCPP CSMS data directory is writable")
    start = task_position(cutover, "Enable and start activated OCPP CSMS service")

    assert init < database < transactions < writable < start


def test_stable_command_is_installed_only_after_handoff_processing():
    main = read(ROLE_TASKS / "main.yml")

    cutover = task_position(main, "Perform controlled OCPP CSMS handoff")
    command = task_position(main, "Install stable OCPP CSMS command")
    assertion = task_position(main, "Assert stable OCPP CSMS command target")

    assert cutover < command < assertion
    assert "/usr/local/bin/ocpp-csms" in read(
        ANSIBLE / "roles" / "ocpp_csms" / "defaults" / "main.yml"
    )


def test_base_role_refuses_root_deployment_identity():
    main = read(ROLE_TASKS / "main.yml")

    assert "ansible_user_id != 'root'" in main
    assert "ansible_user_uid | int != 0" in main
    assert "Use Ansible become for privileged host changes" in main
