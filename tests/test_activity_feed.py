from unittest.mock import Mock
from ocpp_csms.activity_feed import enqueue, publish

def test_no_implicit_or_disabled_emit(monkeypatch):
    monkeypatch.delenv("GWAY_ACTIVITY_FEED_EXTERNAL", raising=False)
    assert not publish("enable")
    monkeypatch.setenv("GWAY_ACTIVITY_FEED_EXTERNAL", "1")
    assert not publish("status")
    assert not publish("reload")

def test_local_sink_only(monkeypatch):
    monkeypatch.setenv("GWAY_ACTIVITY_FEED_EXTERNAL", "1")
    runner = Mock(return_value=Mock(returncode=0))
    args = ("csms.wss_enabled", "WSS listener enabled", "info", "2026-11-24T10:00:00Z")
    assert enqueue(*args, repository="arthexis/ocpp-csms", category="ocpp", run=runner)
    first = runner.call_args.args[0]
    assert first[:2] == ["/usr/local/bin/gway-remote", "feed"]
    assert enqueue(*args, repository="arthexis/ocpp-csms", category="ocpp", run=runner)
    assert first == runner.call_args.args[0]
