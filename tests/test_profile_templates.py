import pytest

from ocpp_csms.profile_templates import build_profile, get_profile_template, list_profile_templates


def test_profile_registry_exposes_only_max_power():
    templates = list_profile_templates()
    assert [template.name for template in templates] == ["max-power"]
    template = get_profile_template("max-power")
    assert template is not None
    assert template.parameters == ("--watts [watts]", "--start [ISO-8601 datetime] (optional; default 2000-01-01T00:00:00Z)")
    assert "ChargePointMaxProfile" in template.ocpp_template
    assert "startSchedule: 2000-01-01T00:00:00Z" in template.ocpp_template
    assert "chargingRateUnit: W" in template.ocpp_template
    assert "limit: [watts]" in template.ocpp_template


def test_max_power_builder_maps_watts_to_station_wide_profile():
    connector, profile = build_profile("max-power", watts=60000)
    assert connector == 0
    assert profile["chargingProfilePurpose"] == "ChargePointMaxProfile"
    assert profile["chargingProfileKind"] == "Absolute"
    assert profile["stackLevel"] == 0
    assert profile["chargingSchedule"]["startSchedule"] == "2000-01-01T00:00:00Z"
    assert profile["chargingSchedule"]["chargingRateUnit"] == "W"
    assert profile["chargingSchedule"]["chargingSchedulePeriod"] == [{"startPeriod": 0, "limit": 60000}]


@pytest.mark.parametrize("watts", [0, -1, True])
def test_max_power_builder_requires_positive_integer_watts(watts):
    with pytest.raises(ValueError, match="watts"):
        build_profile("max-power", watts=watts)


def test_max_power_builder_accepts_explicit_start_and_normalizes_to_utc():
    _, profile = build_profile("max-power", watts=60000, start="2026-10-07T01:30:00-06:00")
    assert profile["chargingSchedule"]["startSchedule"] == "2026-10-07T07:30:00Z"


@pytest.mark.parametrize("start", ["2026-10-07", "2026-10-07T01:30:00", "not-a-date"])
def test_max_power_builder_rejects_start_without_valid_timezone(start):
    with pytest.raises(ValueError, match="--start"):
        build_profile("max-power", watts=60000, start=start)
