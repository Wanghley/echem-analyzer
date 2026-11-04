"""Short, readable names for files, curves and runs (no GUI imports, shared by GUI and reports)."""
from __future__ import annotations

import re
from pathlib import Path

from .cv_analysis import CVRun
from .models import KIND_CA, KIND_CV, Curve


def file_short(source: str | Path) -> str:
    """'2026-11-02 B2 GY data.csv' -> 'B2 GY (csv)': drop the date prefix and the word 'data'."""
    p = Path(source)
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}\s+", "", p.stem)
    stem = re.sub(r"\s+data$", "", stem, flags=re.IGNORECASE)
    return f"{stem} ({p.suffix.lstrip('.').lower()})"


def curve_short_label(c: Curve) -> str:
    """Compact name of a curve inside its file ('scan 2', 'std addition GY 505.1mM', ...)."""
    run = c.meta.get("run_title", "") or ""
    if c.kind == KIND_CV:
        pre = "" if run in ("", "Cyclic Voltammetry") else f"{run} · "
        return f"{pre}scan {c.scan}" if c.scan else f"{pre}{c.title}"
    if c.kind == KIND_CA:
        return run or c.title
    return f"{run} · {c.title}" if run else c.title


def curve_run_label(c: Curve) -> str:
    """Compact name including the file, for legends and lists that mix files."""
    return f"{file_short(c.source)} · {curve_short_label(c)}"


def run_label(run: CVRun) -> str:
    """Name of a multi-cycle CV run for combo boxes: 'B2 GY (csv) · Cyclic Voltammetry (5 cycles)'."""
    n = len(run.cycles)
    return f"{file_short(run.source)} · {run.title}  ({n} cycle{'' if n == 1 else 's'})"
