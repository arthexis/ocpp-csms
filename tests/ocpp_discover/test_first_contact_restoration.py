from ocpp_discover import first_contact


def test_capture_function_is_restored_after_success(monkeypatch):
    replacement = lambda interface, seconds: "later\n"
    monkeypatch.setattr(first_contact.core, "capture_passive_tcp", replacement)
    monkeypatch.setattr(first_contact.core, "run_discovery", lambda **kwargs: object())
    first_contact.run_discovery(initial_evidence="first\n", data_dir="data", state_dir="state")
    assert first_contact.core.capture_passive_tcp is replacement
