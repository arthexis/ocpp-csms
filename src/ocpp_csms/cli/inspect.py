"""Read-only charge-point inspection composed from existing evidence and OCPP commands."""
from __future__ import annotations

import argparse
import asyncio
import json

from ocpp_csms.cli.inspection import _features, _reconciliation
from ocpp_csms.control import send_control
from ocpp_csms.status import appliance_status


def add_inspect_command(subcommands):
    parser = subcommands.add_parser("inspect", help="Investigate charger state with passive evidence and safe live queries")
    parser.add_argument("--cp", "--charger", dest="charger", help="Charge point ID")
    parser.add_argument("--all", action="store_true", help="Inspect all known charge points")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", help="Use stored evidence only; send no OCPP commands")
    mode.add_argument("--deep", action="store_true", help="Also query local list version and composite schedule")
    parser.add_argument("--timeout", type=float, default=8.0, help="Timeout per live query in seconds (default: 8)")
    parser.add_argument("-j", "--json", action="store_true")
    return parser


def _targets(snapshot, *, charger, all_chargers):
    chargers = {item.charger_id: item for item in snapshot["chargers"]}
    if charger:
        return [charger]
    if all_chargers:
        return sorted(chargers)
    connected = [cp for cp, item in chargers.items() if item.connected]
    if len(connected) == 1:
        return connected
    if not connected and len(chargers) == 1:
        return list(chargers)
    raise ValueError("specify --cp or --all when no single charge point is identifiable")


async def _safe_query(data_dir, cp, command, timeout, **kwargs):
    try:
        result = await asyncio.wait_for(
            send_control(data_dir, {"command": command, "charger": cp, **kwargs}), timeout=timeout
        )
    except (OSError, ConnectionError, TimeoutError, ValueError) as exc:
        return {"state": "unknown", "reason": type(exc).__name__}
    if not isinstance(result, dict):
        return {"state": "unknown", "reason": "invalid_response"}
    if result.get("error"):
        return {"state": "unknown", "reason": str(result["error"])}
    response = result.get("response")
    if not isinstance(response, dict):
        return {"state": "unknown", "reason": "invalid_response"}
    return {"state": "observed", "response": response}


async def _inspect(data_dir, cp, snapshot, *, offline, deep, timeout):
    charger = next((item for item in snapshot["chargers"] if item.charger_id == cp), None)
    connected = bool(charger and charger.connected)
    reconciliation = _reconciliation(data_dir, cp, None)
    findings = []
    for item in reconciliation:
        if item["assessment"].startswith("Conflict"):
            findings.append({"severity": "warning", "check": "transactions", "message":
                             f"C{item['connector']}: {item['assessment']}; observed {item['observed_status']}; open TX {item['active_transactions']}"})
        elif item["assessment"] in {"Offline/uncertain", "No connector observation"}:
            findings.append({"severity": "unknown", "check": "transactions", "message": item["assessment"]})
    report = {"cp": cp, "connected": connected, "mode": "offline" if offline else "deep" if deep else "default",
              "reconciliation": reconciliation, "queries": {}, "findings": findings}
    if offline or not connected:
        if not offline and not connected:
            findings.append({"severity": "unknown", "check": "connection", "message": "Charger not connected; active queries skipped"})
        return report

    configuration = await _safe_query(data_dir, cp, "config", timeout, keys=["SupportedFeatureProfiles"], force=True)
    report["queries"]["configuration"] = configuration
    if configuration["state"] == "observed":
        report["features"] = _features(configuration["response"])
    else:
        findings.append({"severity": "unknown", "check": "configuration", "message": configuration["reason"]})

    if deep:
        for label, command, kwargs in (
            ("rfid_list", "rfid_version", {}),
            ("composite_schedule", "get_composite_schedule", {"connector": 0, "duration": 60}),
        ):
            result = await _safe_query(data_dir, cp, command, timeout, **kwargs)
            report["queries"][label] = result
            if result["state"] != "observed":
                findings.append({"severity": "unknown", "check": label, "message": result["reason"]})
            elif label == "composite_schedule" and result["response"].get("status") in {"Rejected", "NotSupported"}:
                findings.append({"severity": "unsupported", "check": label, "message": result["response"]["status"]})
    return report


def run_inspect(args):
    if args.timeout <= 0 or args.timeout > 120:
        raise ValueError("--timeout must be greater than 0 and at most 120 seconds")
    if args.all and args.charger:
        raise ValueError("--all and --cp cannot be combined")
    snapshot = appliance_status(args.data_dir)
    targets = _targets(snapshot, charger=args.charger, all_chargers=args.all)
    async def gather():
        # Sequential requests avoid overwhelming flaky chargers.
        result = []
        for cp in targets:
            result.append(await _inspect(args.data_dir, cp, snapshot, offline=args.offline,
                                         deep=args.deep, timeout=args.timeout))
        return result
    reports = asyncio.run(gather())
    if args.json:
        print(json.dumps({"inspections": reports}, ensure_ascii=False))
    else:
        for report in reports:
            print(f"{report['cp']} | {'connected' if report['connected'] else 'offline'} | {report['mode']}")
            for label, query in report["queries"].items():
                print(f"  {label}: {query['state']}" + (f" ({query['reason']})" if query["state"] != "observed" else ""))
            for finding in report["findings"]:
                print(f"  {finding['severity'].upper()}: {finding['check']}: {finding['message']}")
            if not report["findings"]:
                print("  No discrepancies found in checks performed (not a guarantee of charger health).")
    return 0 if not any(f["severity"] == "warning" for r in reports for f in r["findings"]) else 1
