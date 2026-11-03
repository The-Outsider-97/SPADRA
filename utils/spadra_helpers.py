"""Small pure helpers used across SPADRA. Nothing here touches a project, a file or the network."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from hashlib import sha256

SUFFIX = ".spadra"                                     # a SPADRA project is a folder whose name ends in this
UNITS = ("mm", "cm", "m")
MM_PER = {"mm": 1.0, "cm": 10.0, "m": 1000.0}          # millimetres in one drawing unit


def now_iso() -> str:
    """The current UTC time as ``2026-11-02T13:55:01+00:00`` (whole seconds)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slug(value: str) -> str:
    """A name that is safe as a folder or file name on Windows, macOS and Linux: letters, digits and ``._ -`` only, at most 80 characters."""
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "", value).strip().strip(".")
    return cleaned[:80] or "Project"


def sha256_hex(data: bytes) -> str:
    return sha256(data).hexdigest()


def format_bytes(n: int | float | None) -> str:
    """1536 → ``1.5 KB``. Exact below 1 KB; ``–`` for anything that is not a non-negative number."""
    if n is None or isinstance(n, bool) or n != n or n < 0:
        return "–"
    if n < 1024:
        return f"{int(n)} B"
    value, unit = float(n) / 1024.0, "KB"
    for unit in ("KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            break
        value /= 1024.0
    return f"{value:.0f} {unit}" if value >= 100 else f"{value:.1f} {unit}"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


__all__ = ["SUFFIX", "UNITS", "MM_PER", "now_iso", "slug", "sha256_hex", "format_bytes", "clamp"]
