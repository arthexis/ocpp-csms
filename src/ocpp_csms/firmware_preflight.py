"""Read-only preflight evidence for an OCPP firmware update."""
from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit

from ocpp_csms.cli.inspection import _features, _reconciliation
from ocpp_csms.control import send_control
from ocpp_csms.evidence.diagnostics import events_between
from ocpp_csms.status import appliance_status
from ocpp_csms.transactions.query import TransactionQuery


def validate_location(location):
    try:
        parts = urlsplit(location)
        return parts.scheme.lower() in {"http", "https", "ftp", "ftps"} and bool(parts.hostname) and not parts.fragment
    except ValueError:
        return False


def _last_observation(data_dir, cp, action, *, direction="in"):
    rows = events_between(data_dir, charger_id=cp, limit=None)
    for row in reversed(rows):
        if row["kind"] != "ocpp" or row["action"] != action or row["direction"] != direction:
            continue
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            return {"at": row["occurred_at"], "payload": payload}
    return None


async def preflight(data_dir, cp, *, location=None, query_capability=True, timeout=8.0):
    snapshot = appliance_status(data_dir)
    charger = next((item for item in snapshot["chargers"] if item.charger_id == cp), None)
    connected = bool(charger and charger.connected)
    findings = []

    def add(check, state, detail, *, at=None):
        findings.append({"check": check, "state": state, "detail": detail, "at": at})

    add("connection", "ok" if connected else "warning", "Connected" if connected else "Not connected; no active queries attempted",
        at=charger.connected_at if connected else (charger.last_seen if charger else None))
    active = TransactionQuery(data_dir).active(charger=cp)
    add("transactions", "warning" if active else "ok",
        f"Recorded open transaction IDs: {sorted(tx.transaction_id for tx in active)}" if active else "No open transactions recorded")
    conflicts = [r for r in _reconciliation(data_dir, cp, None) if str(r["assessment"]).startswith("Conflict")]
    add("reconciliation", "warning" if conflicts else "ok",
        f"{len(conflicts)} conflicting connector observations" if conflicts else "No connector conflicts detected")

    boot = _last_observation(data_dir, cp, "BootNotification")
    firmware = boot["payload"].get("firmware_version") if boot else None
    add("installed_firmware", "ok" if firmware else "unknown",
        str(firmware) if firmware else "No firmware version observed in BootNotification",
        at=boot["at"] if boot else None)

    status = _last_observation(data_dir, cp, "FirmwareStatusNotification")
    last_status = status["payload"].get("status") if status else None
    failed = last_status in {"DownloadFailed", "InstallationFailed"}
    add("firmware_history", "warning" if failed else "ok" if status else "unknown",
        str(last_status) if last_status else "No prior firmware status recorded",
        at=status["at"] if status else None)

    if location is None:
        add("location", "unknown", "No firmware location supplied")
    elif validate_location(location):
        add("location", "ok", "URL syntax valid; charger-side reachability and compatibility unverified")
    else:
        add("location", "error", "Invalid firmware URL")

    if not connected:
        add("capability", "unknown", "Charger offline; capability not queried")
    elif not query_capability:
        add("capability", "unknown", "Live capability query disabled")
    else:
        try:
            response = await asyncio.wait_for(
                send_control(data_dir, {"command": "config", "charger": cp,
                                         "keys": ["SupportedFeatureProfiles"], "force": True}),
                timeout=timeout,
            )
            if not isinstance(response, dict) or response.get("error") or not isinstance(response.get("response"), dict):
                add("capability", "unknown", "GetConfiguration unavailable or returned an error")
            else:
                feature = next((x["status"] for x in _features(response["response"])
                                if x["feature"] == "Firmware management"), "Unknown")
                add("capability", "ok" if feature == "Advertised" else "unknown",
                    f"FirmwareManagement: {feature}; advertisement is not compatibility proof")
        except (OSError, ConnectionError, TimeoutError, ValueError):
            add("capability", "unknown", "GetConfiguration failed or timed out")

    assessment = ("Blocked" if any(f["state"] == "error" for f in findings) else
                  "Warnings" if any(f["state"] == "warning" for f in findings) else "No known blockers")
    return {"cp": cp, "assessment": assessment, "findings": findings,
            "compatibility": "unverified", "read_only": True}
