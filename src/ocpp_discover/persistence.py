from __future__ import annotations

import os
from pathlib import Path

from ocpp_discover import redirect
from ocpp_discover.redirect import RedirectReceipt

DEFAULT_STATE_DIR = Path("/var/lib/ocpp-discover")
DEFAULT_DISCOVERED_PATH = DEFAULT_STATE_DIR / "discovered.json"
DEFAULT_RULESET_PATH = Path("/etc/ocpp-discover/redirect.nft")
DEFAULT_NFTABLES_CONFIG = Path("/etc/nftables.conf")
INCLUDE_LINE = 'include "/etc/ocpp-discover/redirect.nft"'


def render_persistent_ruleset(receipt: RedirectReceipt) -> str:
    """Render the exact already-validated Discover redirect for boot loading."""
    return redirect.render_ruleset(receipt)


def _atomic_write(path: Path, content: str, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(temporary, flags, mode)
    except FileExistsError:
        raise RuntimeError("persistent_adaptation_temporary_exists") from None
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def persist_ruleset(
    receipt: RedirectReceipt,
    *,
    ruleset_path: str | Path = DEFAULT_RULESET_PATH,
) -> Path:
    """Persist only the exact narrow ruleset that has already passed handoff validation."""
    path = Path(ruleset_path)
    ruleset = render_persistent_ruleset(receipt)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing != ruleset:
            raise RuntimeError("conflicting_persistent_nftables_adaptation")
        return path
    _atomic_write(path, ruleset, mode=0o600)
    return path


def ensure_nftables_include(
    *,
    config_path: str | Path = DEFAULT_NFTABLES_CONFIG,
    include_line: str = INCLUDE_LINE,
) -> bool:
    """Add Discover's one owned include to Debian's base nftables configuration."""
    path = Path(config_path)
    try:
        original = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        original = "#!/usr/sbin/nft -f\n\n"
    lines = original.splitlines()
    if include_line in lines:
        return False
    content = original
    if content and not content.endswith("\n"):
        content += "\n"
    content += f"\n# OCPP Discover validated adaptation\n{include_line}\n"
    _atomic_write(path, content, mode=0o644)
    return True


def remove_nftables_include(
    *,
    config_path: str | Path = DEFAULT_NFTABLES_CONFIG,
    include_line: str = INCLUDE_LINE,
) -> bool:
    """Remove only Discover's include and adjacent owned comment."""
    path = Path(config_path)
    if not path.exists():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    if include_line not in lines:
        return False
    output: list[str] = []
    for line in lines:
        if line == include_line:
            if output and output[-1] == "# OCPP Discover validated adaptation":
                output.pop()
                if output and output[-1] == "":
                    output.pop()
            continue
        output.append(line)
    _atomic_write(path, "\n".join(output) + "\n", mode=0o644)
    return True


def remove_ruleset(*, ruleset_path: str | Path = DEFAULT_RULESET_PATH) -> bool:
    path = Path(ruleset_path)
    if not path.exists():
        return False
    path.unlink()
    try:
        path.parent.rmdir()
    except OSError:
        pass
    return True
