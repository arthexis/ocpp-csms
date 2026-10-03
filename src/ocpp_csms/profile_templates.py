from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileTemplate:
    name: str
    description: str
    parameters: tuple[str, ...]
    ocpp_template: str


MAX_POWER = ProfileTemplate(
    name="max-power",
    description="Set a station-wide maximum charging power in watts.",
    parameters=("--watts [watts]",),
    ocpp_template="""SetChargingProfile
  connectorId: 0
  csChargingProfiles:
    chargingProfilePurpose: ChargePointMaxProfile
    chargingProfileKind: Absolute
    chargingSchedule:
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
