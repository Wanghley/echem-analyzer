"""Small time formatting helpers (no Qt)."""
from __future__ import annotations

import time
from datetime import datetime
from typing import Optional


def humanize_age(then: float, now: Optional[float] = None) -> str:
    """``"just now"``, ``"5 minutes ago"``, ``"yesterday"``, ``"3 weeks ago"`` or a date, for a Unix time ``then``."""
    now = time.time() if now is None else now
    d = max(now - then, 0.0)                                   # a file stamped slightly in the future is "just now"
    plural = lambda n, w: f"{n} {w}{'' if n == 1 else 's'} ago"  # noqa: E731
    if d < 45:
        return "just now"
    if d < 45 * 60:
        return plural(max(round(d / 60), 1), "minute")
    if d < 22 * 3600:
        return plural(round(d / 3600), "hour")
    if d < 36 * 3600:
        return "yesterday"
    days = round(d / 86400)
    if days < 7:
        return plural(days, "day")
    if days < 35:
        return plural(round(days / 7), "week")
    return datetime.fromtimestamp(then).strftime("%d %b %Y").lstrip("0")


def human_bytes(n: float) -> str:
    """``12.3 MB`` style size."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} GB"
