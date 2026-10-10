"""Recovery CLI and policy stay conservative and do not mutate on preview."""
import asyncio
import json
import sqlite3
from argparse import Namespace
from datetime import datetime, timedelta, timezone

import pytest

from ocpp_csms.cli.recovery import run_recovery
from ocpp_csms.evidence.store import EventStore
from ocpp_csms.recovery import RecoveryPolicy, read_policy, write_policy
from ocpp_csms.schema import database_path
from ocpp_csms.transactions import TransactionArchive
from ocpp_csms.transaction_query import TransactionQuery


def args(data, **kwargs):
    defaults = dict(data_dir=str(data), charger=None, dry_run=False, timeout_minutes=None,
                    interval_minutes=None, policy=False, enable=False, disable=False)
    defaults.update(kwargs)
    return Namespace(**defaults)


def test_policy_persists_and_rejects_invalid_values(tmp_path):
    assert read_policy(tmp_path).timeout_seconds == 3600
    write_policy(tmp_path, RecoveryPolicy(enabled=False, timeout_seconds=1800, interval_seconds=60))
    assert read_policy(tmp_path) == RecoveryPolicy(False, 1800, 60)
    assert run_recovery(args(tmp_path, policy=True, enable=True, timeout_minutes=45)) == 0
    assert read_policy(tmp_path).enabled is True
    assert read_policy(tmp_path).timeout_seconds == 2700
    with pytest.raises(ValueError):
        run_recovery(args(tmp_path, policy=True, timeout_minutes=0))
    (tmp_path / "recovery-policy.json").write_text('{"enabled": 1}', encoding="utf-8")
    with pytest.raises(ValueError):
        read_policy(tmp_path)


def test_recover_preview_and_explicit_offline_write(tmp_path, capsys):
    now = datetime.now(timezone.utc)
    old = (now - timedelta(hours=3)).isoformat()
    async def seed():
        archive = TransactionArchive(tmp_path)
        event = EventStore(tmp_path)
        start = {"connector_id": 1, "id_tag": "TEST",
                 "meter_start": 0, "timestamp": old}
        txid = await archive.start("SIM001", start)
        event.record_transaction_start(txid, "SIM001", start)
        with sqlite3.connect(database_path(tmp_path)) as db:
            db.execute("UPDATE transactions SET last_activity_at=? WHERE transaction_id=?", (old, txid))
            db.execute("INSERT INTO runtime_events (occurred_at,event,charger_id) VALUES (?,?,?)",
                       (old, "charger_disconnected", "SIM001"))
        view = TransactionQuery(tmp_path).get(txid)
        record = view.record
        record.update(created_at=old, updated_at=old, start_received_at=old)
        view.path.write_text(json.dumps(record), encoding="utf-8")
        return txid
    txid = asyncio.run(seed())
    assert run_recovery(args(tmp_path, charger="SIM001", dry_run=True)) == 0
    assert "Eligible: 1" in capsys.readouterr().out
    assert TransactionQuery(tmp_path).get(txid).status == "open"
    assert run_recovery(args(tmp_path, charger="OTHER")) == 0
    assert TransactionQuery(tmp_path).get(txid).status == "open"
    assert run_recovery(args(tmp_path, charger="SIM001")) == 0
    assert TransactionQuery(tmp_path).get(txid).status == "disconnected"
    assert run_recovery(args(tmp_path, charger="SIM001", dry_run=True)) == 0
    assert "Eligible: 0" in capsys.readouterr().out


def test_recover_rejects_running_csms(tmp_path, monkeypatch):
    EventStore(tmp_path)
    monkeypatch.setattr("ocpp_csms.cli.recovery.process_is_running", lambda data: True)
    with pytest.raises(ValueError, match="stop CSMS"):
        run_recovery(args(tmp_path))
    assert run_recovery(args(tmp_path, dry_run=True)) == 0
