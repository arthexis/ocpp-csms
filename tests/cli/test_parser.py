import pytest

from ocpp_csms.cli import build_parser


@pytest.mark.parametrize(
    ("argv", "command"),
    [
        (["init"], "init"),
        (["serve"], "serve"),
        (["start", "--id-tag", "REMOTE"], "start"),
        (["stop", "--transaction", "1"], "stop"),
        (["reboot"], "reboot"),
        (["config"], "config"),
        (["profile", "list"], "profile"),
        (["status"], "status"),
        (["transactions"], "transactions"),
        (["transaction"], "transactions"),
        (["txns"], "transactions"),
        (["txn"], "transactions"),
        (["events"], "events"),
        (["explain", "charger-a"], "explain"),
        (["help"], "help"),
    ],
)
def test_public_command_matrix(argv, command):
    parser, _ = build_parser()
    assert parser.parse_args(argv).command == command


@pytest.mark.parametrize("topic", ["transactions", "transaction", "txns", "txn"])
def test_help_topic_accepts_transaction_aliases(topic):
    parser, _ = build_parser()
    args = parser.parse_args(["help", topic])
    assert args.command == "help"
    assert args.topic == topic
