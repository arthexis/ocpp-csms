from __future__ import annotations

import sys

from ocpp_discover import discover, service


def main(argv: list[str] | None = None) -> int:
    """Dispatch the installed OCPP Discover command surface."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "service":
        return service.main(arguments[1:])
    return discover.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
