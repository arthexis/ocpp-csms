from ocpp_discover import first_contact


def test_seed_is_not_modified(monkeypatch):
    seed = "seed\n"
    monkeypatch.setattr(first_contact.core, "capture_passive_tcp", lambda interface, seconds: "")
    seen = []
    monkeypatch.setattr(first_contact.core, "run_discovery", lambda **kwargs: seen.append(first_contact.core.capture_passive_tcp("eth0", 1)) or object())
    first_contact.run_discovery(initial_evidence=seed, data_dir="data", state_dir="state")
    assert seen == [seed]
