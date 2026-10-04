from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any, Iterable

from field.protocol import send_control


@dataclass(frozen=True)
class Probe:
    name: str
    connector: int
    duration: int
    unit: str


PROBES = (
    Probe("A", 0, 3600, "W"),
    Probe("B", 1, 3600, "W"),
    Probe("C", 2, 3600, "W"),
    Probe("D", 0, 60, "W"),
    Probe("E", 0, 300, "W"),
    Probe("F", 0, 300, "A"),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m field.smart_charging_probe",
        description="Run the issue #56 read-only GetCompositeSchedule diagnostic matrix.",
    )
    parser.add_argument("--control-socket", required=True)
    parser.add_argument("--charger", help="Explicit charger ID when more than one charger is connected")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all probes instead of stopping after the first Accepted response",
    )
    return parser


def request_for(probe: Probe, charger: str | None = None) -> dict[str, Any]:
    request: dict[str, Any] = {
        "command": "get_composite_schedule",
        "connector": probe.connector,
        "duration": probe.duration,
        "charging_rate_unit": probe.unit,
    }
    if charger is not None:
        request["charger"] = charger
    return request


def response_status(response: dict[str, Any]) -> str:
    if response.get("error"):
        return f"error:{response['error']}"
    payload = response.get("response")
    if not isinstance(payload, dict):
        return "invalid_response"
    return str(payload.get("status") or "Unknown")


def run_matrix(
    control_socket: str,
    *,
    charger: str | None = None,
    probes: Iterable[Probe] = PROBES,
    stop_on_accept: bool = True,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for probe in probes:
        request = request_for(probe, charger)
        response = send_control(control_socket, request)
        result = {
            "probe": probe.name,
            "connector": probe.connector,
            "duration": probe.duration,
            "unit": probe.unit,
            "status": response_status(response),
            "response": response,
        }
        results.append(result)
        if stop_on_accept and result["status"] == "Accepted":
            break
    return results


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    results = run_matrix(
        args.control_socket,
        charger=args.charger,
        stop_on_accept=not args.all,
    )
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0 if any(result["status"] == "Accepted" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
