from ocpp_discover import discover


def test_tcp_discovery_lives_in_production_module():
    assert discover.__name__ == "ocpp_discover.discover"
