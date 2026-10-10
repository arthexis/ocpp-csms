"""Read-only comparison of saved and live OCPP configuration snapshots."""
import asyncio
import json
from pathlib import Path
from ocpp_csms.config_report import REDACTED, configuration_snapshot, sensitive_configuration_key
from ocpp_csms.control import send_control

def _load(path):
    try:
        result = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read snapshot {path}: {exc}") from exc
    if not isinstance(result, dict) or not isinstance(result.get("configuration"), list):
        raise ValueError(f"invalid configuration snapshot: {path}")
    return result

def _entries(snapshot):
    result = {}
    for row in snapshot["configuration"]:
        if not isinstance(row, dict) or not isinstance(row.get("key"), str) or not isinstance(row.get("readonly"), bool):
            raise ValueError("invalid configuration snapshot entry")
        key, value = row["key"], row.get("value")
        if value is not None and not isinstance(value, str):
            raise ValueError("invalid configuration snapshot value")
        if key in result:
            raise ValueError(f"duplicate configuration key: {key}")
        result[key] = {"value": REDACTED if sensitive_configuration_key(key) else value, "readonly": row["readonly"]}
    return result

def compare_snapshots(before, after):
    left, right = _entries(before), _entries(after)
    changes = []
    for key in sorted(left.keys() | right.keys()):
        old, new = left.get(key), right.get(key)
        if old != new:
            changes.append({"key": key, "change": "added" if old is None else "removed" if new is None else "changed",
                            "before": old, "after": new})
    return changes

def run_config_diff(args):
    paths = args.items[1:]
    if len(paths) not in (1, 2):
        raise ValueError("config diff requires one snapshot file (live comparison) or two snapshot files")
    if len(paths) == 2 and args.charger:
        raise ValueError("--cp is only valid when comparing one file with the live charger")
    before = _load(paths[0])
    if len(paths) == 2:
        after, mode = _load(paths[1]), "files"
    else:
        charger = args.charger or before.get("charger")
        if not charger:
            raise ValueError("snapshot has no charge point ID; specify --cp")
        try:
            response = asyncio.run(send_control(args.data_dir, {"command": "config", "charger": charger, "force": bool(args.force)}))
        except (OSError, ConnectionError, ValueError) as exc:
            raise ValueError(f"live configuration query failed: {exc}") from exc
        if not isinstance(response, dict) or response.get("error") or not isinstance(response.get("response"), dict):
            reason = response.get("error", "invalid response") if isinstance(response, dict) else "invalid response"
            raise ValueError(f"live configuration query failed: {reason}")
        after = configuration_snapshot(response["response"], charger=charger)
        mode = "live"
    changes = compare_snapshots(before, after)
    if args.json:
        print(json.dumps({"mode": mode, "before": before.get("charger"), "after": after.get("charger"),
                          "different": bool(changes), "changes": changes}, ensure_ascii=False))
    else:
        print(f"Configuration diff ({mode}): {len(changes)} difference(s)")
        for item in changes:
            old = item["before"]["value"] if item["before"] is not None else "(missing)"
            new = item["after"]["value"] if item["after"] is not None else "(missing)"
            print(f"  {item['key']}: {old!s} -> {new!s} [{item['change']}]")
    return 1 if changes else 0
