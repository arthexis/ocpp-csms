from ocpp_discover import first_contact


def test_initial_capture_survives_when_bounded_capture_is_empty(monkeypatch):
    monkeypatch.setattr(first_contact.core, "capture_passive_tcp", lambda interface, seconds: "")
    observed = []
    monkeypatch.setattr(first_contact.core, "run_discovery", lambda **kwargs: observed.append(first_contact.core.capture_passive_tcp("eth0", 1)) or object())
    first_contact.run_discovery(initial_evidence="only-packet\n", data_dir="data", state_dir="state")
    assert observed == ["only-packet\n"]
