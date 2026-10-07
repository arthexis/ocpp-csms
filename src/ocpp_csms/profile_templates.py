from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class ProfileTemplate:
    name: str
    description: str
    parameters: tuple[str, ...]
    ocpp_template: str


MAX_POWER = ProfileTemplate(
    name="max-power",
    description="Set a station-wide maximum charging power in watts.",
    parameters=("--watts [watts]", "--start [ISO-8601 datetime] (optional; default 2000-01-01T00:00:00Z)"),
    ocpp_template="""SetChargingProfile
  connectorId: 0
  csChargingProfiles:
    chargingProfileId: 1
    stackLevel: 0
    chargingProfilePurpose: ChargePointMaxProfile
    chargingProfileKind: Absolute
    chargingSchedule:
      startSchedule: 2000-01-01T00:00:00Z
      chargingRateUnit: W
      chargingSchedulePeriod:
        - startPeriod: 0
          limit: [watts]""",
)


PROFILE_TEMPLATES = {MAX_POWER.name: MAX_POWER}


def list_profile_templates() -> tuple[ProfileTemplate, ...]:
    return tuple(PROFILE_TEMPLATES[name] for name in sorted(PROFILE_TEMPLATES))


def get_profile_template(name: str) -> ProfileTemplate | None:
    return PROFILE_TEMPLATES.get(name)


DEFAULT_START = "2000-01-01T00:00:00Z"


def normalize_start(value: str | None) -> str:
    if value is None:
        return DEFAULT_START
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("--start must be an ISO-8601 datetime with timezone") from exc
    if parsed.tzinfo is None:
        raise ValueError("--start must include a timezone")
    parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def build_profile(name: str, *, watts: int, start: str | None = None) -> tuple[int, dict[str, object]]:
    if name != MAX_POWER.name:
        raise ValueError(f"unknown profile template: {name}")
    if isinstance(watts, bool) or not isinstance(watts, int) or watts <= 0:
        raise ValueError("--watts must be greater than zero")
    start_schedule = normalize_start(start)
    return 0, {
        "chargingProfileId": 1,
        "stackLevel": 0,
        "chargingProfilePurpose": "ChargePointMaxProfile",
        "chargingProfileKind": "Absolute",
        "chargingSchedule": {
            "startSchedule": start_schedule,
            "chargingRateUnit": "W",
            "chargingSchedulePeriod": [
                {"startPeriod": 0, "limit": watts},
            ],
        },
    }


def format_profile_template_list() -> str:
    return "\n".join(f"{template.name}\t{template.description}" for template in list_profile_templates())


def format_profile_template_help(template: ProfileTemplate) -> str:
    parameters = "\n".join(f"  {parameter}" for parameter in template.parameters)
    return (
        f"{template.name}\n\n"
        f"{template.description}\n\n"
        f"Parameters:\n{parameters}\n\n"
        f"OCPP mapping:\n\n{template.ocpp_template}"
    )
