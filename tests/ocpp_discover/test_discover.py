from ocpp_discover import discover


def test_module_is_production_discover():
    assert discover.__name__ == "ocpp_discover.discover"
