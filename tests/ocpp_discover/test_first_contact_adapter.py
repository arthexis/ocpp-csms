import pytest

from ocpp_discover import first_contact


def test_first_contact_requires_evidence():
    with pytest.raises(ValueError, match="initial_evidence_required"):
        first_contact.run_discovery(initial_evidence="", data_dir="data", state_dir="state")
