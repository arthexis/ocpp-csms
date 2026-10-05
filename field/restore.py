"""Compatibility alias for the graduated OCPP Discover package."""

import sys

from ocpp_discover import restore as _implementation

sys.modules[__name__] = _implementation
