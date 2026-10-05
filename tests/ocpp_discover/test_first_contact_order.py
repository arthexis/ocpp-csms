from ocpp_discover import first_contact


def test_first_contact_precedes_later_capture(monkeypatch):
    monkeypatch.setattr(first_contact.core, "capture_passive_tcp", lambda interface, seconds: "second\n")
    observed = []
    monkeypatch.setattr(first_contact.core, "run_discovery", lambda **kwargs: observed.append(first_contact.core.capture_passive_tcp("eth0", 1)) or object())
    first_contact.run_discovery(initial_evidence="first\n", data_dir="data", state_dir="state")
    assert observed == ["first\nsecond\n"]
