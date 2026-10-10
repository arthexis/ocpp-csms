"""Config diff is read-only, works offline, and never reveals sensitive keys."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from ocpp_csms.cli.config_diff import compare_snapshots, run_config_diff


def snapshot(value, *, charger="CP1", password="abc"):
    return {"charger": charger, "configuration": [
        {"key": "HeartbeatInterval", "readonly": False, "value": value},
        {"key": "BackendPassword", "readonly": False, "value": password},
    ], "unknown": []}


def test_diff_does_not_expose_sensitive_values():
    changes = compare_snapshots(snapshot("30", password="secretA"), snapshot("60", password="secretB"))
    assert [row["key"] for row in changes] == ["HeartbeatInterval"]
    assert "secretA" not in str(changes)
    assert "secretB" not in str(changes)


def test_diff_reports_additions_and_removals():
    left = {"configuration": [{"key": "OldKey", "value": "1", "readonly": False}]}
    right = {"configuration": [{"key": "NewKey", "value": "2", "readonly": False}]}
    changes = compare_snapshots(left, right)
    assert [(item["key"], item["change"]) for item in changes] == [("NewKey", "added"), ("OldKey", "removed")]


def test_two_file_diff_never_contacts_charger(monkeypatch, tmp_path, capsys):
    import ocpp_csms.cli.config_diff as module
    send = AsyncMock(side_effect=AssertionError("two-file diff queried charger"))
    monkeypatch.setattr(module, "send_control", send)
    paths = [tmp_path / "a.json", tmp_path / "b.json"]
    for path, value in zip(paths, ("30", "60")):
        path.write_text(json.dumps(snapshot(value)), encoding="utf-8")
    args = SimpleNamespace(items=["diff", *(str(p) for p in paths)], charger=None,
                           data_dir=str(tmp_path), force=False, json=True)
    assert run_config_diff(args) == 1
    assert json.loads(capsys.readouterr().out)["mode"] == "files"
    send.assert_not_awaited()


def test_single_file_queries_charger(monkeypatch, tmp_path, capsys):
    import ocpp_csms.cli.config_diff as module
    send = AsyncMock(return_value={"response": {"configuration_key": [
        {"key": "HeartbeatInterval", "readonly": False, "value": "60"},
        {"key": "BackendPassword", "readonly": False, "value": "secretB"},
    ]}})
    monkeypatch.setattr(module, "send_control", send)
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(snapshot("30")), encoding="utf-8")
    args = SimpleNamespace(items=["diff", str(path)], charger=None,
                           data_dir=str(tmp_path), force=False, json=True)
    assert run_config_diff(args) == 1
    send.assert_awaited_once_with(str(tmp_path), {"command": "config", "charger": "CP1", "force": False})
    output = capsys.readouterr().out
    assert "secretB" not in output
    assert json.loads(output)["mode"] == "live"


def test_diff_rejects_invalid_snapshot(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"configuration":{}}', encoding="utf-8")
    args = SimpleNamespace(items=["diff", str(path), str(path)], charger=None,
                           data_dir=str(tmp_path), force=False, json=False)
    with pytest.raises(ValueError, match="invalid configuration snapshot"):
        run_config_diff(args)


@pytest.mark.asyncio
async def test_inspect_reports_overlapping_transactions(monkeypatch):
    import ocpp_csms.cli.inspect as module
    monkeypatch.setattr(module, "_reconciliation", lambda *a: [{
        "assessment": "Conflict (overlapping transactions)", "connector": 1,
        "observed_status": "Charging", "active_transactions": [42, 43],
    }])
    report = await module._inspect("unused", "CP1", {"chargers": [SimpleNamespace(charger_id="CP1", connected=False)]},
                                  offline=True, deep=False, timeout=1)
    assert report["findings"][0]["severity"] == "warning"
    assert "overlapping" in report["findings"][0]["message"]
