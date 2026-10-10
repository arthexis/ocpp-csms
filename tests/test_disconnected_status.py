"""A disconnected recovery is a presentation label, not a fabricated StopTransaction."""
from ocpp_csms.transactions.query import TransactionView
from ocpp_csms.transactions.formatting import format_transaction, format_transactions


def test_disconnected_label_preserves_persisted_inference(tmp_path):
    record = {"transaction_id": 7, "charge_point_id": "SIM001",
              "status": "inferred_stopped", "stop": None,
              "recovery": {"reason": "disconnected_timeout"}}
    view = TransactionView(record=record, path=tmp_path / "7.json")
    assert view.status == "disconnected"
    assert not view.active
    assert record["status"] == "inferred_stopped"
    assert "disconnected" in format_transactions([view])
    assert "Status:       disconnected" in format_transaction(view)


def test_late_actual_stop_is_not_disconnected(tmp_path):
    view = TransactionView(
        record={"transaction_id": 7, "charge_point_id": "SIM001",
                "status": "stopped", "stop": {"meter_stop": 100}},
        path=tmp_path / "7.json",
    )
    assert view.status == "stopped"
