import json

from ocpp_csms import install_cutover
from ocpp_csms.schema import create_current_schema


def test_schema_action_create_for_missing_database(tmp_path):
    assert install_cutover.schema_action(tmp_path) == "create"


def test_schema_action_current_for_current_database(tmp_path):
    create_current_schema(tmp_path)
    assert install_cutover.schema_action(tmp_path) == "current"


def test_schema_action_upgrade_for_known_old_database(tmp_path, schema_one):
    assert schema_one.exists()
    assert install_cutover.schema_action(tmp_path) == "upgrade"


def test_connection_markers_capture_pre_cutover_event_ids(tmp_path, record_runtime_event):
    create_current_schema(tmp_path)
    record_runtime_event("charger_connected", "a", "a")
    record_runtime_event("charger_connected", "b", "b")
    record_runtime_event("charger_connected", "a", "c")

    assert install_cutover.connection_markers(tmp_path, ("a", "b")) == {"a": 3, "b": 2}


def test_wait_for_reconnect_requires_fresh_connection_event(tmp_path, monkeypatch, record_runtime_event):
    create_current_schema(tmp_path)
    marker = record_runtime_event("charger_connected", "a", "a")
    monkeypatch.setattr(install_cutover.time, "sleep", lambda value: None)
    ticks = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(install_cutover.time, "monotonic", lambda: next(ticks))

    assert install_cutover.wait_for_reconnect(tmp_path, {"a": marker}, timeout=1.0) == ("a",)


def test_wait_for_reconnect_accepts_new_connected_event(tmp_path, record_runtime_event):
    create_current_schema(tmp_path)
    marker = record_runtime_event("charger_connected", "a", "a")
    record_runtime_event("charger_disconnected", "a", "b")
    record_runtime_event("charger_connected", "a", "c")

    assert install_cutover.wait_for_reconnect(tmp_path, {"a": marker}, timeout=0) == ()


def test_capture_baseline_cli_uses_preflight_connected_set(tmp_path, record_runtime_event):
    create_current_schema(tmp_path)
    marker = record_runtime_event("charger_connected", "charger-a", "a")
    preflight = tmp_path / "preflight.json"
    preflight.write_text(json.dumps({"connected_chargers": ["charger-a"]}), encoding="utf-8")
    baseline = tmp_path / "baseline.json"

    assert install_cutover.main([
        "capture-baseline",
        "--data-dir", str(tmp_path),
        "--preflight-json", str(preflight),
        "--output", str(baseline),
    ]) == 0
    assert json.loads(baseline.read_text(encoding="utf-8")) == {"charger-a": marker}
