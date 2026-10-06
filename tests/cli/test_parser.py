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
        (["txn"], "transactions"),
        (["events"], "events"),
        (["explain", "charger-a"], "explain"),
        (["help"], "help"),
    ],
)
def test_public_command_matrix(argv, command):
    parser, _ = build_parser()
    assert parser.parse_args(argv).command == command


def test_help_topic_accepts_transaction_alias():
    parser, _ = build_parser()
    args = parser.parse_args(["help", "txn"])
    assert args.command == "help"
    assert args.topic == "txn"
