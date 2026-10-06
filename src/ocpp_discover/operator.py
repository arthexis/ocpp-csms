from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from ocpp_discover import diagnosis, handoff

DEFAULT_PERSISTENT_DIR = Path("/var/lib/ocpp-discover")
DEFAULT_SERVICE = "ocpp-discover.service"


def _systemctl_state(service: str, action: str) -> str:
    result = subprocess.run(
        ["systemctl", action, service],
        text=True,
        capture_output=True,
        check=False,
    )
    output = result.stdout.strip()
    if output:
        return output
    return "unknown"


def status_report(
    *,
    persistent_dir: str | Path = DEFAULT_PERSISTENT_DIR,
    service: str = DEFAULT_SERVICE,
) -> dict[str, object]:
    """Return cheap, read-only appliance status without requiring durable-state access."""
    path = handoff.discovered_path(persistent_dir)
    return {
        "service": _systemctl_state(service, "is-active"),
        "enabled": _systemctl_state(service, "is-enabled"),
        "persistent_adaptation": "present" if path.exists() else "absent",
        "persistent_path": str(path),
    }


def diagnostics_report(
    *,
    persistent_dir: str | Path = DEFAULT_PERSISTENT_DIR,
    service: str = DEFAULT_SERVICE,
) -> dict[str, object]:
    """Inspect proven adaptation and live/configured network state without mutating it."""
    report = status_report(persistent_dir=persistent_dir, service=service)
    path = handoff.discovered_path(persistent_dir)
    if not path.exists():
        report.update(
            {
                "interface": None,
                "source_ip": None,
                "destination_ips": [],
                "destination_port": None,
                "listen_port": None,
                "configured_matches": None,
                "live_table_present": None,
            }
        )
        return report

    try:
        receipt = handoff.load_discovered(persistent_dir)
    except PermissionError:
        raise RuntimeError("persistent_adaptation_permission_denied") from None

    configuration = diagnosis.inspect_configuration(receipt)
    report.update(
        {
            "interface": receipt.interface,
            "source_ip": receipt.source_ip,
            "destination_ips": list(receipt.destination_ips),
            "destination_port": receipt.destination_port,
            "listen_port": receipt.listen_port,
            "configured_matches": configuration.configured_matches,
            "live_table_present": configuration.live_table_present,
        }
    )
    return report


def _print_report(report: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    labels = {
        "service": "Service",
        "enabled": "Enabled",
        "persistent_adaptation": "Persistent adaptation",
        "interface": "Interface",
        "source_ip": "Source",
        "destination_ips": "Destinations",
        "destination_port": "Destination port",
        "listen_port": "Local listener",
        "configured_matches": "Persistent nftables",
        "live_table_present": "Live nftables",
    }
    for key, label in labels.items():
        if key not in report:
            continue
        value = report[key]
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value) if value else "none"
        elif value is True:
            value = "matches" if key == "configured_matches" else "present"
        elif value is False:
            value = "mismatch" if key == "configured_matches" else "absent"
        elif value is None:
            value = "unknown"
        print(f"{label}: {value}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect the OCPP Discover appliance service.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("status", "Show cheap read-only Discover service and durable-state status."),
        ("diagnostics", "Inspect the proven adaptation and nftables state without mutation."),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--persistent-dir", default=str(DEFAULT_PERSISTENT_DIR))
        command.add_argument("--service", default=DEFAULT_SERVICE)
        command.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "status":
            report = status_report(persistent_dir=args.persistent_dir, service=args.service)
        else:
            report = diagnostics_report(persistent_dir=args.persistent_dir, service=args.service)
    except (RuntimeError, ValueError, OSError) as exc:
        print(str(exc))
        return 1
    _print_report(report, as_json=args.json)
    return 0
