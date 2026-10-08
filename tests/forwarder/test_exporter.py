import json
from types import SimpleNamespace

import pytest

import ocpp_forwarder.exporter as exporter


def test_exporter_calls_only_stable_csms_cli_contract(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "schema": "ocpp-csms/export/v1",
                    "data": {"source_id": "source-a", "cursor": {"after": 7, "next": 8, "more": False}},
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(exporter.subprocess, "run", fake_run)

    result = exporter.read_export(
        "/usr/local/bin/ocpp-csms",
        data_dir="/srv/ocpp",
        after=7,
        limit=500,
    )

    assert seen["argv"] == [
        "/usr/local/bin/ocpp-csms",
        "--data-dir",
        "/srv/ocpp",
        "export",
        "--after",
        "7",
        "--limit",
        "500",
        "--json",
    ]
    assert result["source_id"] == "source-a"


def test_exporter_rejects_wrong_contract(monkeypatch):
    monkeypatch.setattr(
        exporter.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout='{"schema":"other/v1","data":{}}',
            stderr="",
        ),
    )

    with pytest.raises(exporter.ExportError, match="unsupported"):
        exporter.read_export("ocpp-csms", data_dir="/tmp/data", after=0, limit=10)
