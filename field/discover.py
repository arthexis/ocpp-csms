"""Compatibility wrapper for the graduated OCPP Discover package."""

from ocpp_discover.discover import *  # noqa: F401,F403
from ocpp_discover.discover import main


if __name__ == "__main__":
    raise SystemExit(main())
