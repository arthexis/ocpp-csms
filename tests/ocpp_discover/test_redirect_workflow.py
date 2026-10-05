import json

from ocpp_discover import redirect


CAPTURE = """12:00:00.000001 IP 192.168.129.182.40200 > 203.0.113.10.80: Flags [P.], length 180
GET /services/ocppj/CHARGER HTTP/1.1
Host: cloud.example
Upgrade: websocket
Connection: keep-alive, Upgrade

"""


def test_redirect_lifecycle_uses_one_receipt_and_only_dedicated_table(tmp_path, monkeypatch):
    nft_calls = []
    table_present = False

    class Result:
        def __init__(self, returncode=0, stderr=""):
            self.returncode = returncode
            self.stderr = stderr

    def run_nft(command, *, input_text=None):
        nonlocal table_present
        nft_calls.append((command, input_text))
        if command == ["nft", "list", "table", "ip", "ocpp_field_redirect"]:
            if table_present:
                return Result(0)
            return Result(1, "No such file or directory")
        if command == ["nft", "-f", "-"]:
            table_present = True
            return Result(0)
        if command == ["nft", "delete", "table", "ip", "ocpp_field_redirect"]:
            table_present = False
            return Result(0)
        return Result(0)

    monkeypatch.setattr(redirect, "listener_available", lambda port: port == 9000)
    monkeypatch.setattr(redirect, "capture_text", lambda interface, seconds: CAPTURE)
    monkeypatch.setattr(redirect.os, "geteuid", lambda: 0)
    monkeypatch.setattr(redirect, "_run_nft", run_nft)

    captured = redirect.capture("eth0", 9000, 5, tmp_path)
    saved = json.loads((tmp_path / "redirect.json").read_text())

    checked = redirect.validate_ruleset(redirect.load_receipt(tmp_path))
    applied = redirect.apply_redirect(tmp_path)
    redirect.remove_redirect(tmp_path)

    assert saved == captured.to_json()
    assert checked == applied
    assert 'iifname "eth0"' in checked
    assert "ip saddr 192.168.129.182" in checked
    assert "ip daddr { 203.0.113.10 }" in checked
    assert "tcp dport 80 redirect to :9000" in checked
    assert table_present is False

    mutation_commands = [command for command, _ in nft_calls if "-c" not in command and command[:3] != ["nft", "list", "table"]]
    assert mutation_commands == [
        ["nft", "-f", "-"],
        ["nft", "delete", "table", "ip", "ocpp_field_redirect"],
    ]
