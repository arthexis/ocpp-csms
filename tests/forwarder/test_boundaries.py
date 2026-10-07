from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FORWARDER = ROOT / "src" / "ocpp_forwarder"


def test_forwarder_does_not_import_csms_database_implementation():
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in FORWARDER.glob("*.py")
    )

    assert "ocpp_csms.schema" not in text
    assert "ocpp_csms.events" not in text
    assert "sqlite3" not in text


def test_forwarder_uses_ocpp_prefixed_component_package():
    assert FORWARDER.name == "ocpp_forwarder"
    assert (FORWARDER / "__main__.py").exists()
