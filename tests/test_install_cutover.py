from ocpp_csms import install_cutover
from ocpp_csms.schema import create_current_schema


def test_schema_action_create_for_missing_database(tmp_path):
    assert install_cutover.schema_action(tmp_path) == "create"


def test_schema_action_current_for_current_database(tmp_path):
    create_current_schema(tmp_path)
    assert install_cutover.schema_action(tmp_path) == "current"
