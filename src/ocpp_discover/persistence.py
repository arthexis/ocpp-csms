from __future__ import annotations

import os
from pathlib import Path
import subprocess

from ocpp_discover import redirect
from ocpp_discover.redirect import RedirectReceipt

DEFAULT_STATE_DIR = Path("/var/lib/ocpp-discover")
DEFAULT_DISCOVERED_PATH = DEFAULT_STATE_DIR / "discovered.json"
DEFAULT_RULESET_PATH = Path("/etc/ocpp-discover/nftables.conf")
DEFAULT_NFTABLES_CONFIG = Path("/etc/nftables.conf")
INCLUDE_LINE = 'include "/etc/ocpp-discover/nftables.conf"'
EMPTY_RULESET = "# OCPP Discover persistent adaptation\n"
OWNED_INCLUDE_COMMENT = "# OCPP Discover persistent adaptation"


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


def ensure_ruleset_file(*, ruleset_path: str | Path = DEFAULT_RULESET_PATH) -> bool:
    """Create Discover's initially inert nftables fragment without replacing existing state."""
    path = Path(ruleset_path)
    if path.exists():
        return False
    _atomic_write(path, EMPTY_RULESET, mode=0o600)
    return True


def check_nftables_file(path: str | Path, *, nft: str = "nft") -> None:
    """Ask nft to parse/check a file without changing the live kernel ruleset."""
    result = subprocess.run(
        [nft, "--check", "-f", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "nft check failed"
        raise RuntimeError(f"invalid_nftables_configuration: {detail}")


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
        if existing == ruleset:
            return path
        if existing != EMPTY_RULESET:
            raise RuntimeError("conflicting_persistent_nftables_adaptation")
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
    content += f"\n{OWNED_INCLUDE_COMMENT}\n{include_line}\n"
    _atomic_write(path, content, mode=0o644)
    return True


def prepare_nftables_integration(
    *,
    ruleset_path: str | Path = DEFAULT_RULESET_PATH,
    config_path: str | Path = DEFAULT_NFTABLES_CONFIG,
    nft: str = "nft",
) -> tuple[bool, bool]:
    """Install the static Debian nftables integration and validate it without applying it."""
    ruleset = Path(ruleset_path)
    config = Path(config_path)
    old_config = config.read_bytes() if config.exists() else None
    old_mode = (config.stat().st_mode & 0o777) if config.exists() else 0o644
    created_ruleset = ensure_ruleset_file(ruleset_path=ruleset)
    changed_include = False
    try:
        check_nftables_file(ruleset, nft=nft)
        changed_include = ensure_nftables_include(config_path=config)
        check_nftables_file(config, nft=nft)
    except Exception:
        if changed_include:
            if old_config is None:
                config.unlink(missing_ok=True)
            else:
                _atomic_write(config, old_config.decode("utf-8"), mode=old_mode)
        if created_ruleset:
            ruleset.unlink(missing_ok=True)
            try:
                ruleset.parent.rmdir()
            except OSError:
                pass
        raise
    return created_ruleset, changed_include


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
            if output and output[-1] == OWNED_INCLUDE_COMMENT:
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
