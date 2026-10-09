"""Deployment recovery is opt-in and must fail closed."""
from unittest.mock import patch

import pytest

from ocpp_csms.install_recovery import bootstrap_recovery
from ocpp_csms.schema import create_current_schema, inspect_schema


def test_recover_on_empty_fresh_data_is_noop(tmp_path):
    assert bootstrap_recovery(tmp_path) == []
    assert not inspect_schema(tmp_path).exists


def test_recover_on_current_clean_database_is_idempotent(tmp_path):
    create_current_schema(tmp_path)
    assert bootstrap_recovery(tmp_path) == []
    assert bootstrap_recovery(tmp_path) == []


def test_recover_refuses_running_process_before_mutation(tmp_path):
    create_current_schema(tmp_path)
    with patch("ocpp_csms.install_recovery.process_is_running", return_value=True):
        with pytest.raises(RuntimeError, match="stop it"):
            bootstrap_recovery(tmp_path)
    assert inspect_schema(tmp_path).version == 5


def test_recover_refuses_preflight_disagreement(tmp_path):
    create_current_schema(tmp_path)
    from ocpp_csms.install_preflight import InstallPreflight
    with patch("ocpp_csms.install_recovery.evaluate_preflight",
               return_value=InstallPreflight(False, (), ("SIM001",), "SQLite/archive disagrees")):
        with pytest.raises(RuntimeError, match="disagree"):
            bootstrap_recovery(tmp_path)
