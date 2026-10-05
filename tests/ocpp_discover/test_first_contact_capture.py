from ocpp_discover import first_contact


SYN = "12:00:00.000000 IP 192.168.50.20.50000 > 192.168.50.1.8888: Flags [S], seq 1\n"


def test_only_initial_syn_is_available_to_first_capture(monkeypatch):
    captures = []
    replacement = lambda interface, seconds: ""
    monkeypatch.setattr(first_contact.core, "capture_passive_tcp", replacement)

    def run_discovery(**kwargs):
        captures.append(first_contact.core.capture_passive_tcp("enp7s0", 5))
        return object()

    monkeypatch.setattr(first_contact.core, "run_discovery", run_discovery)
    first_contact.run_discovery(initial_evidence=SYN, data_dir="data", state_dir="state")
    assert captures == [SYN]
    assert first_contact.core.capture_passive_tcp is replacement
