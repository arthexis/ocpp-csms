import sqlite3

import pytest

from ocpp_csms.node_identity import configured_node_id
from ocpp_csms.schema import create_current_schema, source_id


def test_default_node_id_uses_hostname(tmp_path, monkeypatch):
    monkeypatch.setattr("ocpp_csms.node_identity.socket.gethostname", lambda: "gway-005")
    assert configured_node_id(tmp_path / "missing.toml") == "gway-005"


def test_node_override(tmp_path):
    config = tmp_path / "node.toml"
    config.write_text('[node]\nid = "test-gateway"\n')
    assert configured_node_id(config) == "test-gateway"


def test_reject_invalid_node_ids(tmp_path):
    config = tmp_path / "node.toml"
    config.write_text('[node]\nid = "not valid"\n')
    with pytest.raises(ValueError, match="Node ID"):
        configured_node_id(config)


def test_fresh_database_uses_host_and_existing_identity_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr("ocpp_csms.node_identity.socket.gethostname", lambda: "gway-005")
    create_current_schema(tmp_path)
    assert source_id(tmp_path) == "gway-005"
    monkeypatch.setattr("ocpp_csms.node_identity.socket.gethostname", lambda: "renamed-host")
    assert source_id(tmp_path) == "gway-005"
