from ocpp_discover import first_contact


def test_initial_evidence_is_prepended_only_once(monkeypatch):
    replacement = lambda interface, seconds: "new\n"
    monkeypatch.setattr(first_contact.core, "capture_passive_tcp", replacement)
    captures = []

    def run_discovery(**kwargs):
        captures.append(first_contact.core.capture_passive_tcp("eth0", 1))
        captures.append(first_contact.core.capture_passive_tcp("eth0", 1))
        return object()

    monkeypatch.setattr(first_contact.core, "run_discovery", run_discovery)
    first_contact.run_discovery(initial_evidence="first\n", data_dir="data", state_dir="state")
    assert captures == ["first\nnew\n", "new\n"]
