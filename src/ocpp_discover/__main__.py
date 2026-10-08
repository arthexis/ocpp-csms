from __future__ import annotations

import argparse
import sys

from ocpp_discover import discover, operator, service, tls_observe


def _help_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocpp-discover",
        description="Operate and inspect the resident OCPP Discover service.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        help="status, diagnostics, service, run, cleanup, or tls-observe",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch the installed OCPP Discover command surface."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        _help_parser().print_help()
        return 0

    command = arguments[0]
    if command in {"status", "diagnostics"}:
        return operator.main(arguments)
    if command == "tls-observe":
        return tls_observe.main(arguments[1:])
    if command == "service":
        return service.main(arguments[1:])
    if command in {"run", "cleanup"}:
        return discover.main(arguments)
    if command in {"-h", "--help"}:
        _help_parser().print_help()
        return 0

    print(f"Unknown command: {command}", file=sys.stderr)
    _help_parser().print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
