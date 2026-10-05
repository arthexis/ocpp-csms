from ocpp_discover import first_contact


def test_adapter_only_changes_capture_for_duration_of_discovery(monkeypatch):
    replacement = lambda interface, seconds: "later\n"
    monkeypatch.setattr(first_contact.core, "capture_passive_tcp", replacement)
    observed = []
    monkeypatch.setattr(first_contact.core, "run_discovery", lambda **kwargs: observed.append(first_contact.core.capture_passive_tcp("eth0", 1)) or object())
    first_contact.run_discovery(initial_evidence="first\n", data_dir="data", state_dir="state")
    assert observed == ["first\nlater\n"]
    assert first_contact.core.capture_passive_tcp is replacement
