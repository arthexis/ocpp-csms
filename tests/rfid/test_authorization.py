from ocpp_csms.rfid.authorization import authorize_rfid, load_rfid_authorization


def test_missing_file_allows_all(tmp_path):
    policy = load_rfid_authorization(tmp_path)

    assert policy.allow_all is True
    assert policy.source is None
    assert authorize_rfid(tmp_path, "anything") == "Accepted"


def test_one_rfid_per_line_is_valid_allow_list(tmp_path):
    (tmp_path / "rfid.csv").write_text("CARD-A\nCARD-B\n", encoding="utf-8")

    policy = load_rfid_authorization(tmp_path)

    assert policy.valid is True
    assert set(policy.entries) == {"CARD-A", "CARD-B"}
    assert authorize_rfid(tmp_path, "CARD-A") == "Accepted"
    assert authorize_rfid(tmp_path, "CARD-X") == "Invalid"


def test_optional_header_can_reorder_columns(tmp_path):
    (tmp_path / "rfid.csv").write_text(
        "name,enabled,rfid\nAlice,true,CARD-A\nFormer,false,CARD-B\n",
        encoding="utf-8",
    )

    policy = load_rfid_authorization(tmp_path)

    assert policy.entries["CARD-A"].name == "Alice"
    assert policy.entries["CARD-A"].enabled is True
    assert authorize_rfid(tmp_path, "CARD-B") == "Blocked"


def test_headerless_extended_rows_use_fixed_order(tmp_path):
    (tmp_path / "rfid.csv").write_text(
        "CARD-A,Alice,true\nCARD-B,Former,false\n",
        encoding="utf-8",
    )

    policy = load_rfid_authorization(tmp_path)

    assert policy.entries["CARD-A"].name == "Alice"
    assert authorize_rfid(tmp_path, "CARD-B") == "Blocked"


def test_blank_lines_comments_and_utf8_bom_are_tolerated(tmp_path):
    (tmp_path / "rfid.csv").write_text(
        "\ufeff# field cards\n\nrfid,name\n CARD-A , Alice \n",
        encoding="utf-8",
    )

    policy = load_rfid_authorization(tmp_path)

    assert policy.valid is True
    assert policy.entries["CARD-A"].name == "Alice"


def test_duplicate_or_malformed_file_is_invalid_and_blocks(tmp_path):
    (tmp_path / "rfid.csv").write_text(
        "rfid,name,enabled\nCARD-A,Alice,true\nCARD-A,Again,true\n",
        encoding="utf-8",
    )

    policy = load_rfid_authorization(tmp_path)

    assert policy.valid is False
    assert "duplicate RFID" in (policy.error or "")
    assert authorize_rfid(tmp_path, "CARD-A") == "Blocked"
