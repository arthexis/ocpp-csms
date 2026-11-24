"""Quiet WSS transitions; at most one local enqueue worker."""
import hashlib
import os
import subprocess

def enqueue(kind, title, severity, stamp, *, repository, category, run=subprocess.run):
    """Opt-in local queue only. No remote or primary operation retry."""
    if os.environ.get("GWAY_ACTIVITY_FEED_EXTERNAL") != "1":
        return False
    try:
        identity = hashlib.sha256((repository + ":" + kind + ":" + stamp).encode()).hexdigest()
        result = run(["/usr/local/bin/gway-remote", "feed", title, "--type", kind,
                      "--category", category, "--severity", severity, "--repo", repository,
                      "--id", identity, "--time", stamp], timeout=1.5, check=False,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return result.returncode == 0
    except Exception:
        return False

from datetime import datetime, timezone
import threading
_slot = threading.Lock()

def publish(action):
    if action not in ("enable", "disable") or os.environ.get("GWAY_ACTIVITY_FEED_EXTERNAL") != "1":
        return False
    if not _slot.acquire(blocking=False):
        return False
    def worker():
        try:
            enqueue("csms.wss_" + ("enabled" if action == "enable" else "disabled"),
                    "WSS listener " + ("enabled" if action == "enable" else "disabled"),
                    "info", datetime.now(timezone.utc).isoformat(),
                    repository="arthexis/ocpp-csms", category="ocpp")
        finally:
            _slot.release()
    try:
        threading.Thread(target=worker, daemon=True, name="csms-feed").start()
        return True
    except Exception:
        _slot.release()
        return False
