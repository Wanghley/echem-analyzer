"""CH Instruments text exports (``.txt``), preview.

The first line is the date, the second the technique.  ``Key = value`` lines hold the parameters, ``Key: text`` lines hold
notes such as ``Instrument Model``, and a column line like ``Potential/V, Current/A`` precedes the comma-separated data.
A CV is one long trace of all its sweeps; it is cut at the reversal points into scans.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .. import techniques
from ..models import DataFile
from . import Reader, register
from ._common import (build_curves, current_scale, find_column, impedance_curve, is_number, number, read_text, rows_to_array,
                      unit_of)

_COLUMN_LINE = re.compile(r"^\s*(Potential|Time|Freq)\s*/\s*\w+\s*,", re.I)


def sniff(head: str) -> bool:
    return bool(re.search(r"(?m)^\s*Instrument Model\s*:", head)) and any(_COLUMN_LINE.match(ln) for ln in head.splitlines())


def _param(params: dict[str, str], prefix: str) -> tuple[Optional[float], str]:
    """(value, unit) of the first ``Key (unit) = value`` parameter whose key starts with ``prefix``."""
    for key, raw in params.items():
        if key.lower().startswith(prefix):
            v = number(raw)
            if v == v:
                return v, unit_of(key)
    return None, ""


def _text_param(params: dict[str, str], prefix: str) -> str:
    return next((v for k, v in params.items() if k.lower().startswith(prefix)), "")


def _technique(title: str, names: list[str]) -> str:
    tech = techniques.classify(title)
    if tech != techniques.OTHER:
        return tech
    has = lambda pat: find_column(names, pat) is not None                                     # noqa: E731
    if has(r"^freq"):
        return techniques.EIS
    if has(r"^potential") and has(r"^current"):
        return techniques.LSV
    if has(r"^time") and has(r"^current"):
        return techniques.CA
    return techniques.OCP if has(r"^time") and has(r"^potential") else techniques.OTHER


def load(path: str | Path) -> DataFile:
    path = Path(path)
    lines = read_text(path).splitlines()
    head_at = next((k for k, ln in enumerate(lines) if _COLUMN_LINE.match(ln)), None)
    if head_at is None:
        raise ValueError(f"{path.name}: no column line (such as 'Potential/V, Current/A') in this CH Instruments file")
    title = lines[1].strip() if len(lines) > 1 else ""
    params: dict[str, str] = {}
    notes: dict[str, str] = {}
    for ln in lines[2:head_at]:
        eq = re.match(r"^\s*(.+?)\s*=\s*(.*?)\s*$", ln)
        if eq:
            params[eq.group(1)] = eq.group(2)
        elif ":" in ln:
            key, _, value = ln.partition(":")
            notes[key.strip().lower()] = value.strip()

    names = [c.strip() for c in lines[head_at].split(",")]
    rows = [ln.split(",") for ln in lines[head_at + 1:] if ln.strip() and is_number(ln.split(",")[0])]
    data = rows_to_array(rows, len(names))
    if data.shape[0] < 2:
        raise ValueError(f"{path.name}: no data rows after the column line")
    tech = _technique(title, names)
    meta = {"timestamp": lines[0].strip(), "device": notes.get("instrument model", "")}

    def column(*patterns: str):
        i = find_column(names, *patterns)
        return None if i is None else (data[:, i], unit_of(names[i]))

    if tech == techniques.EIS:
        f, zr, zi = column(r"^freq"), column(r"^z'|^z′|^zre"), column(r"^z\"|^z''|^z″|^zim")
        c = impedance_curve(path, title, f[0], zr[0], zi[0], meta) if f and zr and zi else None
        curves = [c] if c else []
    else:
        e, i, t = column(r"^potential"), column(r"^current"), column(r"^time")
        e_v = None if e is None else e[0] * (1e-3 if e[1].lower() == "mv" else 1.0)
        scale = current_scale(i[1]) if i else None
        i_ua = None if i is None else i[0] * (scale if scale is not None else 1e6)
        t_s = None if t is None else t[0] * (1e-3 if t[1].lower() == "ms" else 1.0)

        rate = None
        if tech == techniques.CV:
            value, unit = _param(params, "scan rate")
            rate = None if value is None else (value / 1000.0 if unit.lower() == "mv/s" else value)
            begin, high, low = (_param(params, p)[0] for p in ("init e", "high e", "low e"))
            first_up = _text_param(params, "init p/n").strip().upper() != "N"
            for key, v in (("e_begin", begin), ("e_vtx1", high if first_up else low), ("e_vtx2", low if first_up else high),
                           ("e_step", _param(params, "sample interval")[0])):
                if v is not None:
                    meta[key] = v
        elif tech == techniques.CA:
            e0 = _param(params, "init e")[0]
            if e0 is not None:
                meta["E"] = e0
        curves = build_curves(path, tech, title, t=t_s, e=e_v, i_ua=i_ua, meta=meta, rate=rate)

    if not curves:
        raise ValueError(f"{path.name}: no curves found in this CH Instruments file")
    for k, c in enumerate(curves):
        c.uid = f"{path.name}#{k}"
    return DataFile(path=path, fmt="chi", curves=curves, exported_at=lines[0].strip())


register(Reader("chi", "CH Instruments text export (.txt)", (".txt",), sniff, load, verified=False))
