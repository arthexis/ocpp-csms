"""Offline characterization of import boundaries before internal package moves.

No live connections, services, deployment or production database are accessed.
"""
from __future__ import annotations

import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest


@pytest.mark.parametrize(("module", "symbols"), [
    ("ocpp_csms.status", ("ConnectorStatus", "ChargerStatus", "appliance_status", "format_status", "operational_state")),
    ("ocpp_csms.status_contract", ("STATUS_SCHEMA", "status_contract")),
    ("ocpp_csms.energy_query", ("EnergySample", "EnergyQuery", "_entry_sample")),
    ("ocpp_csms.energy_contract", ("SCHEMA", "energy_contract")),
    ("ocpp_csms.export_contract", ("SCHEMA", "export_contract", "_attach_status_provenance")),
    ("ocpp_csms.schema", ("CURRENT_SCHEMA_VERSION", "DATABASE_FILENAME", "source_id", "inspect_schema")),
    ("ocpp_csms.schema_storage", ("StoragePlan", "plan_storage", "prepare_storage")),
    ("ocpp_csms.persistent_wal", ("persistent_wal",)),
    ("ocpp_csms.tls_config", ("TLSConfig", "read_config", "write_config")),
    ("ocpp_csms.tls_listener", ("TLSListener", "DrainingListener")),
    ("ocpp_csms.auth", ("load_auth", "basic_upgrade_check")),
    ("ocpp_csms.firmware_preflight", ("preflight", "validate_location")),
    ("ocpp_csms.diagnostic_upload", ("prepare", "record_response", "inspect_file")),
    ("ocpp_csms.diagnose_receiver", ("main", "Handler")),
    ("ocpp_discover.service", ("_validate",)),
    ("ocpp_csms.install_cutover", ("connection_markers", "wait_for_reconnect")),
])
def test_existing_module_symbols_are_importable(module, symbols):
    imported = importlib.import_module(module)
    for symbol in symbols:
        assert hasattr(imported, symbol), f"{module}.{symbol}"


def test_cross_module_aliases_and_private_patch_seams(monkeypatch):
    from ocpp_csms import energy_query, export_contract, firmware_preflight
    from ocpp_csms import status, status_contract
    from ocpp_csms.cli import inspection
    from ocpp_discover import service
    from ocpp_csms import install_cutover

    assert status_contract.ChargerStatus is status.ChargerStatus
    assert status_contract.ConnectorStatus is status.ConnectorStatus
    assert export_contract._entry_sample is energy_query._entry_sample
    assert firmware_preflight._features is inspection._features
    assert firmware_preflight._reconciliation is inspection._reconciliation
    assert service.connection_markers is install_cutover.connection_markers

    sentinel = object()
    monkeypatch.setattr(export_contract, "_entry_sample", sentinel)
    assert export_contract._entry_sample is sentinel
    assert energy_query._entry_sample is not sentinel
    monkeypatch.setattr(firmware_preflight, "_features", sentinel)
    assert firmware_preflight._features is sentinel
    assert inspection._features is not sentinel


def test_offline_status_energy_export_contracts_and_sqlite_authority(tmp_path):
    from ocpp_csms.evidence.store import EventStore
    from ocpp_csms.status import appliance_status
    from ocpp_csms.status_contract import status_contract
    from ocpp_csms.energy_query import EnergyQuery
    from ocpp_csms.energy_contract import SCHEMA as ENERGY_SCHEMA
    from ocpp_csms.export_contract import SCHEMA as EXPORT_SCHEMA, export_contract
    from ocpp_csms.schema import CURRENT_SCHEMA_VERSION, DATABASE_FILENAME

    assert CURRENT_SCHEMA_VERSION == 8
    assert (ENERGY_SCHEMA, EXPORT_SCHEMA) == ("ocpp-csms/energy/v1", "ocpp-csms/export/v1")
    store = EventStore(tmp_path)
    assert (tmp_path / DATABASE_FILENAME).is_file()
    store.record_ocpp("cp-baseline", "Heartbeat", {})
    status = appliance_status(tmp_path)
    assert isinstance(status, dict)
    status_json = status_contract(status)
    assert status_json["schema"] == "ocpp-csms/status/v1"
    assert isinstance(EnergyQuery(tmp_path), EnergyQuery)

    first = export_contract(tmp_path, after=0, limit=1)
    repeated = export_contract(tmp_path, after=0, limit=1)
    assert first["schema"] == EXPORT_SCHEMA
    assert first["data"]["events"] == repeated["data"]["events"]
    assert first["data"]["source_id"] == repeated["data"]["source_id"]
    assert first["data"]["cursor"] == {"after": 0, "next": 1, "more": False}
    assert export_contract(tmp_path, after=1, limit=1)["data"]["cursor"] == {
        "after": 1, "next": 1, "more": False,
    }


def test_offline_cli_export_entrypoint(tmp_path, monkeypatch, capsys):
    from ocpp_csms.cli import main
    from ocpp_csms.schema import create_current_schema

    create_current_schema(tmp_path)
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", "--data-dir", str(tmp_path), "export", "-j"])
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "ocpp-csms/export/v1"
    assert payload["data"]["cursor"] == {"after": 0, "next": 0, "more": False}


def test_installed_distribution_entrypoints():
    """Editable installs must retain both published script entrypoints."""
    distribution = metadata.distribution("ocpp-csms")
    scripts = {item.name: item.value for item in distribution.entry_points if item.group == "console_scripts"}
    assert scripts["ocpp-csms"] == "ocpp_csms.cli:main"
    assert scripts["ocpp-discover"] == "ocpp_discover.__main__:main"


def test_offline_wheel_contains_both_console_scripts(tmp_path):
    """Build a local wheel only when the backend is present offline.

    The existing CI installs with isolated PEP 517 builds, so setuptools may
    not be available in the pytest interpreter itself.
    """
    try:
        importlib.import_module("setuptools.build_meta")
    except ImportError:
        pytest.skip("offline wheel build requires setuptools in test interpreter")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-index", "--no-deps",
         "--no-build-isolation", "--wheel-dir", str(tmp_path), str(root)],
        capture_output=True, text=True, timeout=90, check=False,
        env={**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"},
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    wheels = list(tmp_path.glob("ocpp_csms-*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        assert "ocpp_csms/status.py" in names
        assert "ocpp_csms/export_contract.py" in names
        assert "ocpp_discover/service.py" in names
        entry = next(name for name in names if name.endswith(".dist-info/entry_points.txt"))
        config = wheel.read(entry).decode("utf-8")
        assert "ocpp-csms = ocpp_csms.cli:main" in config
        assert "ocpp-discover = ocpp_discover.__main__:main" in config

    # Install the genuine built wheel only into a disposable directory. No
    # repository install, service, network index, or target-host writes.
    target = tmp_path / "installed-wheel"
    installed = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
         "--no-compile", "--target", str(target), str(wheels[0])],
        capture_output=True, text=True, timeout=90, check=False,
        cwd=str(tmp_path),
        env={**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INPUT": "1"},
    )
    assert installed.returncode == 0, installed.stdout + "\n" + installed.stderr
    for name in ("ocpp-csms", "ocpp-discover"):
        assert (target / "bin" / name).is_file()
    probe_code = (
        "import importlib, pathlib, sys\n"
        "root = pathlib.Path(sys.argv[1]).resolve()\n"
        "for name in ('ocpp_csms', 'ocpp_discover'):\n"
        "    package = importlib.import_module(name)\n"
        "    assert pathlib.Path(package.__file__).resolve().is_relative_to(root)\n"
        "import ocpp_csms.cli as cli\n"
        "from ocpp_discover.__main__ import main\n"
        "cli.cli_data_dir = lambda: str(root)\n"
        "assert 'txn' in cli.build_parser()[1]\n"
        "assert callable(main)\n"
    )
    probe = subprocess.run(
        [sys.executable, "-c", probe_code, str(target)],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=30,
        check=False, env={**os.environ, "PYTHONPATH": str(target)},
    )
    assert probe.returncode == 0, probe.stdout + "\n" + probe.stderr


@pytest.mark.parametrize(("module", "symbols"), [
    ("ocpp_csms.cli.parser", ("add_appliance_commands", "add_config_command", "add_control_commands", "add_diagnostic_commands", "add_profile_command")),
    ("ocpp_csms.contracts", ("SUPPORTED_CONTRACTS", "DIAGNOSTIC_CONTRACTS", "ALL_CONTRACTS")),
    ("ocpp_csms.control", ("ControlServer", "Session", "SessionRegistry", "dispatch_control", "send_control")),
    ("ocpp_csms.transactions.archive", ("TransactionArchive", "default_data_dir")),
])
def test_legacy_facades_and_compatibility_symbols_are_importable(module, symbols):
    exposed = importlib.import_module(module)
    assert all(hasattr(exposed, symbol) for symbol in symbols)


def test_reexport_identity_and_intentional_control_patch_seam():
    import ocpp_csms.control as control
    from ocpp_csms import contracts
    from ocpp_csms.cli import appliance, parser
    from ocpp_csms.status_contract import STATUS_SCHEMA

    assert parser.add_appliance_commands is appliance.add_appliance_commands
    assert contracts.SUPPORTED_CONTRACTS["status"] == STATUS_SCHEMA
    assert contracts.ALL_CONTRACTS["events_raw"] == contracts.DIAGNOSTIC_CONTRACTS["events_raw"]
    assert hasattr(control, "os")  # existing monkeypatch seam, not dead code


def test_tls_static_placeholder_and_live_status_are_distinct(tmp_path):
    from ocpp_csms import tls_config
    from ocpp_csms.tls_listener import TLSListener

    config_path = tmp_path / "no-tls.json"
    # Captures existing JSON semantics; not an endorsement of the stale label.
    assert tls_config.status(config_path)["listener"] == "not_implemented"
    assert TLSListener(config_path).status()["listener"] == "stopped"


def test_legacy_cli_txn_and_reservation_dispatch_remains_offline(tmp_path, monkeypatch):
    import ocpp_csms.cli as cli

    monkeypatch.setattr(cli, "cli_data_dir", lambda: str(tmp_path))
    observed = []

    def stub_control(args):
        observed.append(("stop", args.transaction, args.data_dir))
        return 17

    def stub_explain(args):
        observed.append(("explain", args.transaction_id, args.data_dir))
        return 18

    def stub_reservation(args):
        observed.append(("reserve", args.reservation_id, args.data_dir))
        return 19

    def stub_query(args):
        observed.append(("txn", args.transaction_id, args.data_dir))
        return 20

    monkeypatch.setattr(cli, "run_control", stub_control)
    monkeypatch.setattr(cli, "run_txn_explain", stub_explain)
    monkeypatch.setattr(cli, "run_reservation", stub_reservation)
    monkeypatch.setattr(cli, "run_transactions_json", stub_query)
    scenarios = (
        ("stop", ["txn", "stop", "23"], 17),
        ("explain", ["txn", "explain", "23"], 18),
        ("reserve", ["reserve", "23", "--json"], 19),
        ("txn", ["txn", "23", "--json"], 20),
    )
    for label, arguments, expected in scenarios:
        monkeypatch.setattr(sys, "argv", ["ocpp-csms", "--data-dir", str(tmp_path), *arguments])
        assert cli.main() == expected
        assert observed[-1] == (label, 23, str(tmp_path))

    # Invalid operations fail inside argparse before any command reaches transport.
    count = len(observed)
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", "--data-dir", str(tmp_path), "txn", "stop"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert len(observed) == count


def test_export_paging_and_orphan_stop_stay_sqlite_authoritative(tmp_path):
    from ocpp_csms.evidence.store import EventStore
    from ocpp_csms.export_contract import export_contract
    from ocpp_csms.transactions.query import TransactionQuery

    store = EventStore(tmp_path)
    store.record_ocpp("cp-one", "Heartbeat", {})
    store.record_ocpp("cp-two", "Heartbeat", {})
    store.commit_transaction_evidence(
        "cp-orphan", "StopTransaction",
        {"transaction_id": 9001, "meter_stop": 125, "timestamp": "2026-11-01T01:00:00Z"},
    )
    first = export_contract(tmp_path, after=0, limit=2)
    again = export_contract(tmp_path, after=0, limit=2)
    second = export_contract(tmp_path, after=2, limit=2)
    assert first["data"]["cursor"] == {"after": 0, "next": 2, "more": True}
    assert second["data"]["cursor"] == {"after": 2, "next": 3, "more": False}
    assert first["data"]["events"] == again["data"]["events"]
    assert first["data"]["source_id"] == second["data"]["source_id"]
    assert len(first["data"]["events"]) == 2
    assert len(second["data"]["events"]) == 1
    assert TransactionQuery(tmp_path).get(9001) is None  # no invented Start
