"""Cross-command numeric and time-range validation contracts."""
from datetime import datetime, timezone

import pytest

from ocpp_csms.cli.options import require_nonnegative, require_positive, validate_time_order


@pytest.mark.parametrize("value", [None, 0, 1])
def test_nonnegative_accepts_valid_values(value):
    require_nonnegative(value, name="connector")


def test_nonnegative_rejects_negative():
    with pytest.raises(ValueError, match="connector must be zero or greater"):
        require_nonnegative(-1, name="connector")


@pytest.mark.parametrize("value", [0, -1, None])
def test_positive_rejects_nonpositive(value):
    with pytest.raises(ValueError, match="--limit must be at least 1"):
        require_positive(value, name="--limit")


def test_positive_accepts_one():
    require_positive(1, name="--limit")


def test_time_order_validation():
    earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
    later = datetime(2026, 1, 2, tzinfo=timezone.utc)
    validate_time_order(earlier, later)
    validate_time_order(None, later)
    with pytest.raises(ValueError, match="lower time bound"):
        validate_time_order(later, earlier)
