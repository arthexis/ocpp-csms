from __future__ import annotations

from dataclasses import replace

import pytest

from ocpp_forwarder.forwarder import Forwarder
from ocpp_forwarder.state import ForwarderState, StateStore


class FakeCollector:
    def __init__(self, *, fail_resource=None):
        self.fail_resource = fail_resource
        self.calls = []

    def upsert(self, resource, rows, *, on_conflict):
        self.calls.append(("upsert", resource, rows, on_conflict))
        if resource == self.fail_resource:
            raise RuntimeError(f"failed {resource}")

    def update_satellite(self, satellite_id, values):
        self.calls.append(("satellite", satellite_id, values))
        if self.fail_resource == "satellites":
            raise RuntimeError("failed satellites")


def page(
    *,
    source_id="source-a",
    after=0,
    next_cursor=1,
    more=False,
    event_id=1,
):
    return {
        "source_id": source_id,
        "cursor": {"after": after, "next": next_cursor, "more": more},
        "events": [
            {
                "id": event_id,
                "at": "2026-10-07T20:00:00Z",
                "charger_id": "charger-a",
                "kind": "meter_values",
                "action": "MeterValues",
                "transaction_id": 7,
            }
        ],
        "energy": [
            {
                "source_event_id": event_id,
                "sample_index": 0,
                "at": "2026-10-07T20:00:00Z",
                "charger_id": "charger-a",
                "connector_id": 1,
                "transaction_id": 7,
                "power_w": 7200.0,
                "energy_wh": 1500.0,
            }
        ],
        "transactions": [
            {
                "id": 7,
                "charger_id": "charger-a",
                "connector_id": 1,
                "rfid": "CARD-A",
                "state": "open",
                "active": True,
                "started_at": "2026-10-07T19:59:00Z",
                "stopped_at": None,
                "meter_start_wh": 1000,
                "meter_stop_wh": None,
                "energy_wh": None,
                "last_activity_at": "2026-10-07T20:00:00Z",
            }
        ],
        "status": {
            "chargers": [
                {
                    "id": "charger-a",
                    "connected": True,
                    "connected_at": "2026-10-07T19:00:00Z",
                    "last_seen": "2026-10-07T20:00:00Z",
                    "protocol": "ocpp1.6",
                    "status": "Charging",
                    "error_code": None,
                }
            ]
        },
    }


def build(tmp_path, pages, collector=None):
    calls = []

    def reader(command, *, data_dir, after, limit):
        calls.append((command, data_dir, after, limit))
        value = pages.pop(0)
        assert value["cursor"]["after"] == after
        return value

    store = StateStore(tmp_path / "state.json")
    forwarder = Forwarder(
        satellite_id="gway-004",
        csms_command="/usr/local/bin/ocpp-csms",
        data_dir="/srv/ocpp",
        batch_size=500,
        state_store=store,
        collector=collector or FakeCollector(),
        export_reader=reader,
    )
    return forwarder, store, calls


def test_first_sync_starts_at_cursor_zero_and_saves_after_all_writes(tmp_path):
    collector = FakeCollector()
    forwarder, store, calls = build(tmp_path, [page()], collector)

    state, more = forwarder.forward_once()

    assert calls == [("/usr/local/bin/ocpp-csms", "/srv/ocpp", 0, 500)]
    assert state.cursor == 1
    assert more is False
    assert store.load().cursor == 1
    assert [call[1] for call in collector.calls[:4]] == [
        "events",
        "energy_samples",
        "transactions",
        "chargers",
    ]
    assert collector.calls[-1][0] == "satellite"


def test_forwarded_rows_are_scoped_to_satellite_and_source(tmp_path):
    collector = FakeCollector()
    forwarder, _, _ = build(tmp_path, [page()], collector)

    forwarder.forward_once()

    for call in collector.calls[:4]:
        for row in call[2]:
            assert row["satellite_id"] == "gway-004"
            assert row["source_id"] == "source-a"


@pytest.mark.parametrize("resource", ["events", "energy_samples", "transactions", "chargers", "satellites"])
def test_partial_collector_failure_never_advances_cursor(tmp_path, resource):
    collector = FakeCollector(fail_resource=resource)
    forwarder, store, _ = build(tmp_path, [page()], collector)
    store.save(ForwarderState(source_id="source-a", cursor=0))

    with pytest.raises(RuntimeError):
        forwarder.forward_once()

    assert store.load().cursor == 0


def test_restart_resumes_from_saved_cursor(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.save(ForwarderState(source_id="source-a", cursor=40))
    calls = []

    def reader(command, *, data_dir, after, limit):
        calls.append(after)
        return page(after=40, next_cursor=41, event_id=41)

    forwarder = Forwarder(
        satellite_id="gway-004",
        csms_command="ocpp-csms",
        data_dir="/srv/ocpp",
        batch_size=500,
        state_store=store,
        collector=FakeCollector(),
        export_reader=reader,
    )

    forwarder.forward_once()

    assert calls == [40]
    assert store.load().cursor == 41


def test_changed_source_restarts_from_zero_without_overwriting_old_epoch(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.save(ForwarderState(source_id="source-old", cursor=99))
    calls = []
    pages = [
        page(source_id="source-new", after=99, next_cursor=99, event_id=100),
        page(source_id="source-new", after=0, next_cursor=1, event_id=1),
    ]

    def reader(command, *, data_dir, after, limit):
        calls.append(after)
        return pages.pop(0)

    collector = FakeCollector()
    forwarder = Forwarder(
        satellite_id="gway-004",
        csms_command="ocpp-csms",
        data_dir="/srv/ocpp",
        batch_size=500,
        state_store=store,
        collector=collector,
        export_reader=reader,
    )

    state, _ = forwarder.forward_once()

    assert calls == [99, 0]
    assert state.source_id == "source-new"
    assert state.cursor == 1
    event_rows = collector.calls[0][2]
    assert event_rows[0]["source_id"] == "source-new"
    assert event_rows[0]["source_event_id"] == 1


def test_backlog_pages_drain_without_poll_sleep(tmp_path):
    pages = [
        page(after=0, next_cursor=1, more=True, event_id=1),
        page(after=1, next_cursor=2, more=False, event_id=2),
    ]
    forwarder, store, _ = build(tmp_path, pages)
    sleeps = []

    def sleeper(seconds):
        sleeps.append(seconds)
        if store.load().cursor == 2:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        forwarder.run(poll_seconds=10, sleeper=sleeper)

    assert sleeps == [10]


def test_retry_backoff_keeps_cursor_and_resets_after_success(tmp_path):
    store = StateStore(tmp_path / "state.json")
    attempts = iter([RuntimeError("offline"), page()])
    sleeps = []

    def reader(command, *, data_dir, after, limit):
        value = next(attempts)
        if isinstance(value, Exception):
            raise value
        return value

    forwarder = Forwarder(
        satellite_id="gway-004",
        csms_command="ocpp-csms",
        data_dir="/srv/ocpp",
        batch_size=500,
        state_store=store,
        collector=FakeCollector(),
        export_reader=reader,
    )

    def sleeper(seconds):
        sleeps.append(seconds)
        if store.load().cursor == 1:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        forwarder.run(poll_seconds=10, sleeper=sleeper)

    assert sleeps == [1.0, 10]
    assert store.load().cursor == 1
    assert store.load().last_error is None
