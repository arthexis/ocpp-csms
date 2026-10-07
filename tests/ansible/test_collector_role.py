from .helpers import ANSIBLE, load_yaml, read, task_by_name


COLLECTOR = ANSIBLE / "roles" / "ocpp_collector"
DEFAULTS = COLLECTOR / "defaults" / "main.yml"
TASKS = COLLECTOR / "tasks" / "main.yml"
SCHEMA = COLLECTOR / "templates" / "schema.sql.j2"
PLAYBOOK = ANSIBLE / "playbooks" / "collector.yml"
SERVICE = COLLECTOR / "templates" / "ocpp-collector.service.j2"
POSTGREST = COLLECTOR / "templates" / "postgrest.conf.j2"


def test_collector_playbook_is_separate_from_satellite_deployment():
    play = load_yaml(PLAYBOOK)[0]
    assert play["roles"] == [{"role": "ocpp_collector"}]
    assert "ocpp_collector" not in read(ANSIBLE / "playbooks" / "satellite.yml")


def test_collector_reuses_existing_postgres_and_never_installs_server():
    tasks = read(TASKS)
    prerequisites = task_by_name(TASKS, "Install OCPP Collector runtime prerequisites")

    assert prerequisites["ansible.builtin.apt"]["name"] == [
        "libpq5",
        "postgresql-client",
        "xz-utils",
    ]
    assert "postgresql-server" not in tasks
    assert "postgresql-" not in "\n".join(
        item
        for item in prerequisites["ansible.builtin.apt"]["name"]
        if item != "postgresql-client"
    )


def test_collector_supports_explicit_or_local_postgres_resolution():
    defaults = load_yaml(DEFAULTS)
    assert defaults["ocpp_collector_pg_host"] == ""
    assert defaults["ocpp_collector_pg_local_socket_dir"] == "/var/run/postgresql"

    detect = task_by_name(TASKS, "Check local PostgreSQL socket")
    refuse = task_by_name(TASKS, "Refuse to guess or install PostgreSQL")
    resolve = task_by_name(TASKS, "Resolve PostgreSQL endpoint")

    assert "ocpp_collector_pg_autodetected" in detect["when"]
    assert refuse["ansible.builtin.assert"]["that"] == [
        "ocpp_collector_local_pg_socket.stat.exists"
    ]
    resolved = resolve["ansible.builtin.set_fact"]["ocpp_collector_resolved_pg_host"]
    assert "ocpp_collector_pg_local_socket_dir" in resolved
    assert "ocpp_collector_pg_host" in resolved


def test_local_postgres_peer_auth_requires_matching_service_and_db_roles():
    validation = task_by_name(TASKS, "Validate OCPP Collector host and variables")
    conditions = validation["ansible.builtin.assert"]["that"]
    assert (
        "not (ocpp_collector_pg_host | length == 0) or "
        "ocpp_collector_db_role == ocpp_collector_service_user"
    ) in conditions


def test_postgrest_is_pinned_and_checksum_verified_for_supported_architectures():
    defaults = load_yaml(DEFAULTS)
    download = task_by_name(TASKS, "Download pinned PostgREST release")

    assert defaults["ocpp_collector_postgrest_version"] == "16.4"
    assert len(defaults["ocpp_collector_postgrest_x86_64_sha256"]) == 64
    assert len(defaults["ocpp_collector_postgrest_aarch64_sha256"]) == 64
    assert "checksum" in download["ansible.builtin.get_url"]
    assert "linux-static-{{ ocpp_collector_postgrest_arch }}" in download["ansible.builtin.get_url"]["url"]


def test_collector_schema_uses_source_aware_idempotent_keys():
    schema = read(SCHEMA)

    assert "PRIMARY KEY (satellite_id, source_id, source_event_id)" in schema
    assert "PRIMARY KEY (satellite_id, source_id, transaction_id)" in schema
    assert "PRIMARY KEY (satellite_id, source_id, charger_id)" in schema
    assert (
        "PRIMARY KEY (satellite_id, source_id, source_event_id, sample_index)"
        in schema
    )


def test_collector_exposes_only_api_schema_through_postgrest():
    config = read(POSTGREST)
    assert 'db-schemas = "api"' in config
    assert 'db-anon-role = "ocpp_collector_anon"' in config
    assert "server-host" in config
    assert "server-port" in config


def test_collector_service_is_isolated_and_restartable():
    service = read(SERVICE)
    assert "Restart=always" in service
    assert "NoNewPrivileges=true" in service
    assert "ProtectSystem=strict" in service
    assert "ProtectHome=true" in service


def test_proxy_auto_mode_requires_existing_nginx_instead_of_installing_it():
    defaults = load_yaml(DEFAULTS)
    assert defaults["ocpp_collector_proxy_mode"] == "auto"

    inspect = task_by_name(TASKS, "Inspect existing nginx installation")
    refuse = task_by_name(TASKS, "Refuse to guess reverse-proxy infrastructure")
    packages = task_by_name(TASKS, "Install OCPP Collector runtime prerequisites")

    assert inspect["ansible.builtin.stat"]["path"] == "/etc/nginx/nginx.conf"
    assert "ocpp_collector_resolved_proxy_mode != 'missing'" in refuse["ansible.builtin.assert"]["that"]
    assert "nginx" not in packages["ansible.builtin.apt"]["name"]


def test_nginx_configuration_is_validated_before_handler_flush():
    text = read(TASKS)
    assert text.index("- name: Validate nginx configuration before reload") < text.index(
        "- name: Flush OCPP Collector handlers"
    )
