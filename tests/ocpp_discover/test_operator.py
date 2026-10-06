from __future__ import annotations

from types import SimpleNamespace

import pytest

from ocpp_discover import __main__ as command
from ocpp_discover import diagnosis, handoff, operator
from ocpp_discover.redirect import RedirectReceipt, WebSocketRequest


def receipt():
    return RedirectReceipt(
        interface="enp7s0",
        listen_port=9100,
        source_ip="172.16.5.40",
        destination_ips=["172.16.5.1"],
        requests=[WebSocketRequest("172.16.5.1", "172.16.5.1:8080", "/ocpp/CP7")],
        captured_at="2026-10-05T00:00:00+00:00",
        destination_port=8080,
    )


def test_status_is_read_only_and_does_not_load_private_adaptation(tmp_path, monkeypatch):
    persistent = tmp_path / "persistent"
    persistent.mkdir()
    handoff.discovered_path(persistent).write_text("private\n")
    monkeypatch.setattr(operator, "_systemctl_state", lambda service, action: "active" if action == "is-active" else "enabled")
    monkeypatch.setattr(handoff, "load_discovered", lambda *args, **kwargs: pytest.fail("status must not read protected adaptation contents"))

    report = operator.status_report(persistent_dir=persistent)

    assert report["service"] == "active"
    assert report["enabled"] == "enabled"
    assert report["persistent_adaptation"] == "present"


def test_diagnostics_reports_proven_adaptation_without_mutation(tmp_path, monkeypatch):
    persistent = tmp_path / "persistent"
    handoff.persist_discovered(persistent, receipt())
    monkeypatch.setattr(operator, "_systemctl_state", lambda service, action: "active" if action == "is-active" else "enabled")
    monkeypatch.setattr(
        operator.diagnosis,
        "inspect_configuration",
        lambda expected: diagnosis.Configuration(True, True),
    )

    report = operator.diagnostics_report(persistent_dir=persistent)

    assert report["service"] == "active"
    assert report["persistent_adaptation"] == "present"
    assert report["interface"] == "enp7s0"
    assert report["source_ip"] == "172.16.5.40"
    assert report["destination_ips"] == ["172.16.5.1"]
    assert report["destination_port"] == 8080
    assert report["listen_port"] == 9100
    assert report["configured_matches"] is True
    assert report["live_table_present"] is True
    assert handoff.load_discovered(persistent) == receipt()


def test_diagnostics_without_adaptation_is_non_mutating(tmp_path, monkeypatch):
    monkeypatch.setattr(operator, "_systemctl_state", lambda service, action: "active" if action == "is-active" else "enabled")
    monkeypatch.setattr(operator.diagnosis, "inspect_configuration", lambda expected: pytest.fail("no receipt means no network inspection"))

    report = operator.diagnostics_report(persistent_dir=tmp_path / "missing")

    assert report["persistent_adaptation"] == "absent"
    assert report["configured_matches"] is None
    assert report["live_table_present"] is None


def test_global_command_defaults_to_help_instead_of_discovery(monkeypatch, capsys):
    monkeypatch.setattr(command.discover, "main", lambda args: pytest.fail("bare command must not trigger discovery"))
    assert command.main([]) == 0
    assert "status" in capsys.readouterr().out


def test_global_command_routes_status_and_diagnostics(monkeypatch):
    calls = []
    monkeypatch.setattr(command.operator, "main", lambda args: calls.append(args) or 0)
    assert command.main(["status", "--json"]) == 0
    assert command.main(["diagnostics"]) == 0
    assert calls == [["status", "--json"], ["diagnostics"]]


def test_global_command_keeps_explicit_manual_discovery_paths(monkeypatch):
    calls = []
    monkeypatch.setattr(command.discover, "main", lambda args: calls.append(args) or 0)
    assert command.main(["run", "--data-dir", "/tmp/data", "--state-dir", "/tmp/state"]) == 0
    assert command.main(["cleanup", "--state-dir", "/tmp/state"]) == 0
    assert calls[0][0] == "run"
    assert calls[1][0] == "cleanup"
