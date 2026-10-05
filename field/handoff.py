"""Compatibility alias for the graduated OCPP Discover package."""

import sys

from ocpp_discover import handoff as _implementation

sys.modules[__name__] = _implementation
