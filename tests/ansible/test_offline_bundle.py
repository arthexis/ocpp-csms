from .helpers import ANSIBLE, DEFAULTS, TASKS, load_yaml, read, task_by_name


PREPARE = ANSIBLE / "playbooks" / "prepare-release.yml"
OFFLINE = ANSIBLE / "playbooks" / "offline-install.yml"
DISCOVER_TASKS = ANSIBLE / "roles" / "ocpp_discover" / "tasks" / "main.yml"


def test_prepare_release_builds_self_contained_python_bundle():
    prepare = read(PREPARE)

    assert "Download bundle runtime dependency wheels" in prepare
    assert "Build bundle OCPP CSMS application wheel" in prepare
    assert "Copy bundle Python source" in prepare
    assert "Write bundle manifest" in prepare
    assert "--only-binary=:all:" in prepare
    assert "--no-deps" in prepare


def test_offline_playbook_reuses_shared_roles_in_bundle_mode():
    playbook = load_yaml(OFFLINE)[0]

    assert playbook["pre_tasks"][0]["name"] == "Require offline release bundle path"
    roles = playbook["roles"]
    assert roles[0]["role"] == "ocpp_csms"
    assert roles[0]["vars"]["ocpp_csms_install_mode"] == "bundle"
    assert roles[1]["role"] == "ocpp_discover"


def test_bundle_mode_installs_only_from_bundle_wheels():
    runtime = task_by_name(TASKS / "main.yml", "Install bundled runtime dependencies")
    application = task_by_name(TASKS / "main.yml", "Install bundled OCPP CSMS application")

    argv = runtime["ansible.builtin.command"]["argv"]
    assert "--no-index" in argv
    assert "--find-links" in argv
    assert "{{ ocpp_csms_bundle_wheels_dir }}" in argv
    assert runtime["when"][-1] == "ocpp_csms_install_mode == 'bundle'"

    assert application["ansible.builtin.pip"]["extra_args"] == "--no-index --no-deps"
    assert application["when"][-1] == "ocpp_csms_install_mode == 'bundle'"


def test_bundle_mode_uses_manifest_release_and_source():
    defaults = load_yaml(DEFAULTS)
    capture = task_by_name(TASKS / "main.yml", "Capture immutable release identifier")
    source = task_by_name(TASKS / "main.yml", "Select release source for preflight")

    assert defaults["ocpp_csms_install_mode"] == "online"
    assert defaults["ocpp_csms_bundle_manifest_path"].endswith("/manifest.yml")
    assert "ocpp_csms_bundle_manifest.release" in capture["ansible.builtin.set_fact"]["ocpp_csms_release_id"]
    assert "ocpp_csms_bundle_source_dir" in source["ansible.builtin.set_fact"]["ocpp_csms_preflight_source_dir"]


def test_bundle_mode_does_not_touch_online_package_indexes():
    prerequisites = task_by_name(TASKS / "main.yml", "Install Python prerequisites")
    discover_packages = task_by_name(DISCOVER_TASKS, "Install OCPP Discover runtime packages")

    assert prerequisites["when"] == "ocpp_csms_install_mode == 'online'"
    assert discover_packages["when"] == "(ocpp_csms_install_mode | default('online')) == 'online'"


def test_bundle_mode_rejects_dev_and_remote_install():
    validate = task_by_name(TASKS / "main.yml", "Validate OCPP CSMS install mode")
    conditions = validate["ansible.builtin.assert"]["that"]

    assert "ocpp_csms_install_mode != 'bundle' or (ansible_connection | default('ssh')) == 'local'" in conditions
    assert "ocpp_csms_install_mode != 'bundle' or not (ocpp_csms_dev | bool)" in conditions
