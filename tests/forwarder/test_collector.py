from __future__ import annotations

from types import SimpleNamespace

import pytest

import ocpp_forwarder.collector as collector_module
from ocpp_forwarder.collector import CollectorClient, CollectorError


class Response:
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_collector_upsert_uses_bearer_auth_and_merge_duplicates(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["request"] = request
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(collector_module, "urlopen", fake_urlopen)
    client = CollectorClient("https://collector.example", "TOKEN", timeout=7)

    client.upsert(
        "events",
        [{"satellite_id": "gway-004", "source_id": "source-a", "source_event_id": 1}],
        on_conflict="satellite_id,source_id,source_event_id",
    )

    request = seen["request"]
    assert request.full_url.endswith(
        "/events?on_conflict=satellite_id%2Csource_id%2Csource_event_id"
    )
    assert request.get_header("Authorization") == "Bearer TOKEN"
    assert request.get_header("Prefer") == "resolution=merge-duplicates,return=minimal"
    assert seen["timeout"] == 7


def test_collector_http_failure_is_a_forwarder_error(monkeypatch):
    def fail(*args, **kwargs):
        raise collector_module.HTTPError(
            "https://collector.example/events",
            503,
            "unavailable",
            {},
            None,
        )

    monkeypatch.setattr(collector_module, "urlopen", fail)
    client = CollectorClient("https://collector.example", "TOKEN")

    with pytest.raises(CollectorError, match="HTTP 503"):
        client.upsert(
            "events",
            [{"satellite_id": "gway-004"}],
            on_conflict="satellite_id",
        )
