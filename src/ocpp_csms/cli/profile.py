from __future__ import annotations

import argparse
import asyncio

from ocpp_csms.control import send_control
from ocpp_csms.output import emit_json
from ocpp_csms.profile_templates import (
    build_profile,
    format_profile_template_help,
    format_profile_template_list,
    get_profile_template,
)


PROFILE_PURPOSES = ("ChargePointMaxProfile", "TxDefaultProfile", "TxProfile")


def add_profile_command(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    add = argparse.ArgumentParser.add_argument
    profile = subcommands.add_parser("profile", help="Inspect and apply Smart Charging profiles")
    profile.set_defaults(profile_parser=profile)
    profile_subcommands = profile.add_subparsers(dest="profile_command")
    profile_subcommands.add_parser("templates", help="List built-in profile templates")
    profile_help = profile_subcommands.add_parser("help", help="Explain a built-in profile template")
    add(profile_help, "template", help="Built-in profile template name")
    profile_send = profile_subcommands.add_parser("send", help="Send a built-in profile template to the charger")
    add(profile_send, "template", help="Built-in profile template name")
    add(profile_send, "-c", "--charger", help="Explicit charge point ID when more than one charger is connected")
    add(profile_send, "--watts", type=int, required=True, help="Maximum charging power in watts")
    profile_composite = profile_subcommands.add_parser("composite", help="Show the charger's effective composite schedule")
    add(profile_composite, "-c", "--charger", help="Explicit charge point ID when more than one charger is connected")
    add(profile_composite, "--connector", "--cp", dest="connector", type=int, default=0, help="Connector ID (default: %(default)s)")
    add(profile_composite, "--duration", type=int, default=3600, help="Schedule duration in seconds (default: %(default)s)")
    add(profile_composite, "-j", "--json", action="store_true", help="Print the OCPP response as JSON")
    profile_clear = profile_subcommands.add_parser("clear", help="Clear Smart Charging profiles from the charger")
    add(profile_clear, "-c", "--charger", help="Explicit charge point ID when more than one charger is connected")
    add(profile_clear, "--id", dest="profile_id", type=int, help="Clear one chargingProfileId")
    add(profile_clear, "--connector", "--cp", dest="connector", type=int, help="Filter by connector ID")
    add(profile_clear, "--purpose", choices=PROFILE_PURPOSES, help="Filter by charging profile purpose")
    add(profile_clear, "--stack-level", type=int, help="Filter by stack level")
    return profile


def _profile_send_request(args: argparse.Namespace) -> dict[str, object]:
    connector, profile = build_profile(args.template, watts=args.watts)
    request: dict[str, object] = {"command": "set_charging_profile", "connector": connector, "profile": profile}
    if args.charger is not None:
        request["charger"] = args.charger
    return request


def _profile_composite_request(args: argparse.Namespace) -> dict[str, object]:
    if args.connector < 0:
        raise ValueError("--connector/--cp must be zero or greater")
    if args.duration < 1:
        raise ValueError("--duration must be at least 1 second")
    request: dict[str, object] = {"command": "get_composite_schedule", "connector": args.connector, "duration": args.duration}
    if args.charger is not None:
        request["charger"] = args.charger
    return request


def _profile_clear_request(args: argparse.Namespace) -> dict[str, object]:
    if args.profile_id is not None and args.profile_id < 0:
        raise ValueError("--id must be zero or greater")
    if args.connector is not None and args.connector < 0:
        raise ValueError("--connector/--cp must be zero or greater")
    if args.stack_level is not None and args.stack_level < 0:
        raise ValueError("--stack-level must be zero or greater")
    request: dict[str, object] = {"command": "clear_charging_profile"}
    if args.charger is not None:
        request["charger"] = args.charger
    if args.profile_id is not None:
        request["id"] = args.profile_id
    if args.connector is not None:
        request["connector"] = args.connector
    if args.purpose is not None:
        request["purpose"] = args.purpose
    if args.stack_level is not None:
        request["stack_level"] = args.stack_level
    return request


def _schedule_value(mapping: dict[str, object], snake: str, camel: str) -> object:
    return mapping.get(snake, mapping.get(camel))


def _format_single_composite_schedule(payload: dict[str, object]) -> str:
    status = payload.get("status")
    if status != "Accepted":
        return str(status or "Unknown")
    connector = payload.get("connector_id", payload.get("connectorId"))
    start = payload.get("schedule_start", payload.get("scheduleStart"))
    schedule = payload.get("charging_schedule", payload.get("chargingSchedule"))
    if not isinstance(schedule, dict):
        raise ValueError("invalid composite schedule response")
    unit = _schedule_value(schedule, "charging_rate_unit", "chargingRateUnit")
    periods = _schedule_value(schedule, "charging_schedule_period", "chargingSchedulePeriod")
    if not isinstance(periods, list):
        raise ValueError("invalid composite schedule response")
    lines = [f"Connector: {connector}", f"Schedule start: {start}", f"Rate unit: {unit}", "Periods:"]
    for period in periods:
        if not isinstance(period, dict):
            raise ValueError("invalid composite schedule response")
        offset = _schedule_value(period, "start_period", "startPeriod")
        limit = period.get("limit")
        phases = _schedule_value(period, "number_phases", "numberPhases")
        line = f"  +{offset}s\t{limit} {unit}"
        if phases is not None:
            line += f"\t{phases} phase(s)"
        lines.append(line)
    return "\n".join(lines)


def _format_composite_schedule(payload: dict[str, object]) -> str:
    if payload.get("compatibility_fallback") != "physical_connectors":
        return _format_single_composite_schedule(payload)
    schedules = payload.get("schedules")
    if not isinstance(schedules, list):
        raise ValueError("invalid composite schedule fallback response")
    lines = ["Charger does not support aggregate composite schedule on connector 0.", "Showing physical connectors instead."]
    for item in schedules:
        if not isinstance(item, dict):
            raise ValueError("invalid composite schedule fallback response")
        connector_id = item.get("connector_id")
        response = item.get("response")
        if not isinstance(response, dict):
            raise ValueError("invalid composite schedule fallback response")
        lines.extend(["", f"Connector {connector_id}", _format_single_composite_schedule(response)])
    return "\n".join(lines)


def _send_profile_request(args: argparse.Namespace, request: dict[str, object]) -> tuple[int, dict[str, object] | None]:
    try:
        response = asyncio.run(send_control(args.data_dir, request))
    except (ConnectionError, FileNotFoundError, OSError, ValueError) as exc:
        print(f"error: {exc}")
        return 1, None
    error = response.get("error")
    if error:
        detail = response.get("detail") or response.get("charger")
        suffix = f": {detail}" if detail is not None else ""
        print(f"error: {error}{suffix}")
        return 1, None
    payload = response.get("response")
    if not isinstance(payload, dict):
        return 1, None
    return 0, payload


def run_profile(args: argparse.Namespace) -> int:
    if args.profile_command is None:
        args.profile_parser.print_help()
        return 0
    if args.profile_command == "templates":
        print(format_profile_template_list())
        return 0
    if args.profile_command == "help":
        template = get_profile_template(args.template)
        if template is None:
            print(f"error: unknown profile template: {args.template}")
            return 1
        print(format_profile_template_help(template))
        return 0
    if args.profile_command == "send":
        try:
            request = _profile_send_request(args)
        except ValueError as exc:
            print(f"error: {exc}")
            return 1
        code, payload = _send_profile_request(args, request)
        if code or payload is None:
            return 1
        status = payload.get("status")
        print(status or "Unknown")
        return 0 if status == "Accepted" else 1
    if args.profile_command == "composite":
        try:
            request = _profile_composite_request(args)
        except ValueError as exc:
            print(f"error: {exc}")
            return 1
        code, payload = _send_profile_request(args, request)
        if code or payload is None:
            return 1
        if args.json:
            emit_json(payload)
        else:
            try:
                print(_format_composite_schedule(payload))
            except ValueError as exc:
                print(f"error: {exc}")
                return 1
        return 0 if payload.get("status") == "Accepted" else 1
    if args.profile_command == "clear":
        try:
            request = _profile_clear_request(args)
        except ValueError as exc:
            print(f"error: {exc}")
            return 1
        code, payload = _send_profile_request(args, request)
        if code or payload is None:
            return 1
        status = payload.get("status")
        print(status or "Unknown")
        return 0 if status == "Accepted" else 1
    raise ValueError("profile requires a subcommand")
