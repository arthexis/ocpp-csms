from types import SimpleNamespace

import pytest

from ocpp_csms.control import dispatch_control


class Session:
    def __init__(self):
        self.calls = []

    async def get_configuration(self, keys):
        self.calls.append(("rfid_config", tuple(keys)))
        return SimpleNamespace(configuration_key=[], unknown_key=[])

    async def get_local_list_version(self):
        self.calls.append(("rfid_version",))
        return SimpleNamespace(list_version=3)

    async def send_local_list(self, list_version, entries):
        self.calls.append(("rfid_send", list_version, entries))
        return SimpleNamespace(status="Accepted")


class Registry:
    def __init__(self, session=None):
        self.sessions = {"charger-a": session} if session is not None else {}
        self.rfid_lists = []

    def session(self, charge_point_id):
        return self.sessions.get(charge_point_id)

    def connected_chargers(self):
        return sorted(self.sessions)

    def physical_connector_ids(self, charge_point_id):
        return []

    def active_transaction_ids(self, charge_point_id):
        return []

    def record_control_event(self, event, *, charger_id, details=None):
        pass

    def latest_rfid_list_version(self, charger_id):
        versions = [
            item["list_version"]
            for item in self.rfid_lists
            if item["charger_id"] == charger_id
        ]
        return max(versions) if versions else None

    def record_rfid_list(
        self,
        charger_id,
        *,
        list_version,
        entries,
        source_file,
        list_hash,
        verified_version,
    ):
        self.rfid_lists.append(
            {
                "charger_id": charger_id,
                "list_version": list_version,
                "entries": entries,
                "source_file": source_file,
                "list_hash": list_hash,
                "verified_version": verified_version,
            }
        )
        return len(self.rfid_lists)


@pytest.mark.asyncio
async def test_version_queries_charger():
    session = Session()

    response = await dispatch_control(Registry(session), {"command": "rfid_version"})

    assert response == {
        "ok": True,
        "response": {"list_version": 3, "charger": "charger-a", "configuration": {"configuration_key": [], "unknown_key": []}},
    }
    assert session.calls[0][0] == "rfid_version"
    assert session.calls[1][0] == "rfid_config"


@pytest.mark.asyncio
async def test_export_sends_next_full_list_and_records_only_after_acceptance():
    session = Session()
    registry = Registry(session)

    response = await dispatch_control(
        registry,
        {
            "command": "rfid_export",
            "entries": [
                {"rfid": "CARD-A", "name": "Alice", "enabled": True},
                {"rfid": "CARD-B", "name": None, "enabled": True},
            ],
            "source_file": "rfid.csv",
            "list_hash": "hash",
        },
    )

    assert response["response"]["status"] == "Accepted"
    assert response["response"]["previous_version"] == 3
    assert response["response"]["list_version"] == 4
    assert response["response"]["verified_version"] == 3
    assert session.calls == [
        ("rfid_config", tuple(__import__("ocpp_csms.control", fromlist=["RFID_CONFIGURATION_KEYS"]).RFID_CONFIGURATION_KEYS)),
        ("rfid_version",),
        (
            "rfid_send",
            4,
            [
                {"rfid": "CARD-A", "name": "Alice", "enabled": True},
                {"rfid": "CARD-B", "name": None, "enabled": True},
            ],
        ),
        ("rfid_version",),
    ]
    assert registry.rfid_lists[0]["list_version"] == 4
    assert registry.rfid_lists[0]["verified_version"] == 3


@pytest.mark.asyncio
async def test_rejected_export_is_not_stored():
    class RejectingSession(Session):
        async def send_local_list(self, list_version, entries):
            self.calls.append(("rfid_send", list_version, entries))
            return SimpleNamespace(status="Failed")

    session = RejectingSession()
    registry = Registry(session)

    response = await dispatch_control(
        registry,
        {
            "command": "rfid_export",
            "entries": [{"rfid": "CARD-A", "name": None, "enabled": True}],
            "source_file": "rfid.csv",
            "list_hash": "hash",
        },
    )

    assert response["response"]["status"] == "Failed"
    assert registry.rfid_lists == []
    assert session.calls == [
        ("rfid_config", tuple(__import__("ocpp_csms.control", fromlist=["RFID_CONFIGURATION_KEYS"]).RFID_CONFIGURATION_KEYS)),
        ("rfid_version",),
        ("rfid_send", 4, [{"rfid": "CARD-A", "name": None, "enabled": True}]),
    ]


@pytest.mark.asyncio
async def test_export_version_uses_recorded_history_to_avoid_reuse():
    session = Session()
    registry = Registry(session)
    registry.rfid_lists.append(
        {
            "charger_id": "charger-a",
            "list_version": 8,
            "entries": [],
            "source_file": "rfid.csv",
            "list_hash": "old",
            "verified_version": 8,
        }
    )

    response = await dispatch_control(
        registry,
        {
            "command": "rfid_export",
            "entries": [],
            "source_file": "rfid.csv",
            "list_hash": "empty-hash",
        },
    )

    assert response["response"]["list_version"] == 9
    assert ("rfid_send", 9, []) in session.calls


@pytest.mark.asyncio
async def test_clear_sends_empty_full_list_version_zero_and_records_acceptance():
    class ClearSession(Session):
        async def get_local_list_version(self):
            self.calls.append(("rfid_version",))
            return SimpleNamespace(list_version=5 if len(self.calls) == 1 else 0)

    session = ClearSession()
    registry = Registry(session)

    response = await dispatch_control(registry, {"command": "rfid_clear"})

    assert response["response"]["status"] == "Accepted"
    assert response["response"]["list_version"] == 0
    assert response["response"]["verified_version"] == 0
    assert ("rfid_send", 0, []) in session.calls
    assert registry.rfid_lists == [
        {
            "charger_id": "charger-a",
            "list_version": 0,
            "entries": [],
            "source_file": None,
            "list_hash": "empty",
            "verified_version": 0,
        }
    ]
