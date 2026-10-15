from ocpp_csms import install_cutover
from ocpp_csms.schema import create_current_schema


def test_schema_action_create_for_missing_database(tmp_path):
    assert install_cutover.schema_action(tmp_path) == "create"


def test_schema_action_current_for_current_database(tmp_path):
    create_current_schema(tmp_path)
    assert install_cutover.schema_action(tmp_path) == "current"


def test_schema_action_upgrade_for_known_old_database(tmp_path, schema_one):
    assert schema_one.exists()
    assert install_cutover.schema_action(tmp_path) == "upgrade"


