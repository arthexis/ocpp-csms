import pytest

from ocpp_csms.cli import build_parser


@pytest.mark.parametrize(
    ("argv", "command"),
    [
        (["init"], "init"),
        (["serve"], "serve"),
        (["reset", "--now"], "reset"),
        (["config"], "config"),
        (["profile", "templates"], "profile"),
        (["status"], "status"),
        (["txn"], "txn"),
        (["events"], "events"),
        (["explain", "charger-a"], "explain"),
        (["help"], "help"),
    ],
)
def test_public_command_matrix(argv, command):
    parser, _ = build_parser()
    assert parser.parse_args(argv).command == command


@pytest.mark.parametrize("topic", ["txn"])
def test_help_topic_accepts_txn(topic):
    parser, _ = build_parser()
    args = parser.parse_args(["help", topic])
    assert args.command == "help"
    assert args.topic == topic
