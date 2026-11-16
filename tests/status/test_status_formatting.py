def test_status_last_seen_omits_microseconds_and_aligns_long_ids():
    from ocpp_csms.status import ChargerStatus, format_status
    charger = ChargerStatus(
        charger_id="GSCSC0824110052X0124", connected=True,
        connected_at=None, subprotocol=None,
        last_seen="2026-10-15T15:44:42.476504Z",
        status="Available", error_code=None,
        transaction_id=None, id_tag=None, started_at=None, connectors=[],
    )
    lines = format_status({"chargers": [charger], "active_chargers": []}).splitlines()
    header = next(line for line in lines if line.startswith("ID "))
    record = next(line for line in lines if line.startswith(charger.charger_id))
    assert header.index("Connected") == record.index("unknown")
    assert "2026-10-15T15:44:42Z" in record
    assert ".476504" not in record


def test_timestamp_presentation_preserves_raw_evidence():
    from ocpp_csms.status import display_timestamp
    assert display_timestamp("2026-10-15T15:44:42.476504Z") == "2026-10-15T15:44:42Z"
    assert display_timestamp("2026-10-15T15:44:42.476504+00:00") == "2026-10-15T15:44:42+00:00"
    assert display_timestamp(None) == "-"


def test_connected_duration_formats_seconds_minutes_hours_and_days():
    from datetime import datetime, timedelta, timezone
    from ocpp_csms.status import connected_duration

    now = datetime(2026, 10, 15, 16, 0, tzinfo=timezone.utc)
    def duration(seconds):
        return connected_duration(True, (now - timedelta(seconds=seconds)).isoformat(), now=now)

    assert duration(0) == "0s"
    assert duration(59) == "59s"
    assert duration(60) == "1m"
    assert duration(3599) == "59m"
    assert duration(3600) == "1h"
    assert duration(3660) == "1h 1m"
    assert duration(86400 + 40 * 60) == "1d 40m"
    assert duration(86400 + 3600) == "1d 1h"
    assert connected_duration(False, now.isoformat(), now=now) == "no"
    assert connected_duration(True, None, now=now) == "unknown"


def test_status_table_displays_connection_age_not_boolean():
    from datetime import datetime, timedelta, timezone
    from ocpp_csms.status import ChargerStatus, format_status

    now = datetime.now(timezone.utc)
    charger = ChargerStatus(
        charger_id="charger-a", connected=True,
        connected_at=(now - timedelta(minutes=40)).isoformat(),
        subprotocol="ocpp1.6", last_seen=now.isoformat(),
        status="Available", error_code=None, transaction_id=None,
        id_tag=None, started_at=None, connectors=[],
    )
    line = next(line for line in format_status({"chargers": [charger], "active_chargers": []}).splitlines()
                if line.startswith("charger-a"))
    assert "40m" in line
    assert "yes" not in line
