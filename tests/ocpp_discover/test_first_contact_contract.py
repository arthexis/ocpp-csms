from ocpp_discover import first_contact


def test_first_contact_adapter_exposes_run_discovery():
    assert callable(first_contact.run_discovery)
