"""Metrohm Autolab Nova data exports (``.txt`` / ``.csv``), preview.

Nova's "export data" gives one header line of column names (``Time (s)``, ``Potential applied (V)``, ``WE(1).Current (A)``,
``WE(1).Potential (V)``, ``Scan``, ``Index`` or, for impedance, ``Frequency (Hz)``, ``Z' (Ω)``, ``-Z'' (Ω)``) and the rows.
The delimiter is a semicolon, a tab or a comma, and the decimal mark follows the computer's language.  The file does not say
which technique made it, so the technique is read from the columns: a swept potential with a scan column is a CV.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import numpy as np

from .. import techniques
from ..models import DataFile
from . import Reader, register
from ._common import build_curves, current_scale, find_column, impedance_curve, read_text, rows_to_array, unit_of

SWEEP_MIN_V = 0.05                       # a potential that moves less than this is a fixed potential (amperometry)
_HEADER = re.compile(r"WE\(\d+\)\.|Potential applied|Frequency\s*\(Hz\).*Z\s*['′]", re.I)


def _header_at(lines: list[str]) -> Optional[int]:
    return next((k for k, ln in enumerate(lines[:25]) if ln.strip() and _HEADER.search(ln)), None)


def sniff(head: str) -> bool:
    lines = head.lstrip("﻿").splitlines()
    return _header_at(lines) is not None and len(re.split(r"[;\t,]", lines[_header_at(lines)])) >= 2


def _delimiter(header: str) -> str:
    return max((";", "\t", ","), key=header.count)


def load(path: str | Path) -> DataFile:
    path = Path(path)
    lines = read_text(path).lstrip("﻿").splitlines()
    at = _header_at(lines)
    if at is None:
        raise ValueError(f"{path.name}: no Nova column line (such as 'Time (s);WE(1).Current (A)') in this file")
    delim = _delimiter(lines[at])
    names = [c.strip().strip('"') for c in lines[at].split(delim)]
    data = rows_to_array((ln.split(delim) for ln in lines[at + 1:] if ln.strip()), len(names))
    data = data[np.isfinite(data).any(axis=1)] if data.size else data
    if data.shape[0] < 2:
        raise ValueError(f"{path.name}: no data rows in this Nova file")

    def col(*patterns: str) -> Optional[int]:
        return find_column(names, *patterns)

    f_i, zr_i, zi_i = col(r"^frequency"), col(r"^z['′]\s*\(|^re\(z"), col(r"^-?z(''|″|\")|^-?im\(z")
    t_i, c_i, s_i = col(r"^time"), col(r"\.current\b|^current"), col(r"^scan$")
    applied_i, we_i = col(r"^potential applied"), col(r"\.potential\b|^potential\b")
    e_i = applied_i if applied_i is not None else we_i
    meta = {"timestamp": "", "device": ""}

    if f_i is not None and zr_i is not None and zi_i is not None:
        tech = techniques.EIS
        zim = data[:, zi_i] * (-1.0 if names[zi_i].startswith("-") else 1.0)                  # "-Z''" is positive when capacitive
        curve = impedance_curve(path, techniques.label(tech), data[:, f_i], data[:, zr_i], zim, meta)
        curves = [curve] if curve else []
    else:
        e_v = None if e_i is None else data[:, e_i] * (1e-3 if unit_of(names[e_i]).lower() == "mv" else 1.0)
        scale = current_scale(unit_of(names[c_i])) if c_i is not None else None
        i_ua = None if c_i is None else data[:, c_i] * (scale if scale is not None else 1e6)
        t_s = None if t_i is None else data[:, t_i]
        labels = None if s_i is None else data[:, s_i]
        if i_ua is None:
            tech = techniques.OCP if (t_s is not None and e_v is not None) else techniques.OTHER
        elif e_v is not None and np.nanmax(e_v) - np.nanmin(e_v) > SWEEP_MIN_V:
            sweep = np.diff(e_v[np.isfinite(e_v)])
            monotone = bool((sweep >= 0).all() or (sweep <= 0).all())
            tech = techniques.CV if (labels is not None and np.unique(labels[np.isfinite(labels)]).size > 1) or not monotone \
                else techniques.LSV
        else:
            tech = techniques.CA
        if tech == techniques.CA and e_v is not None:
            meta["E"] = float(np.nanmedian(e_v))
        curves = build_curves(path, tech, techniques.label(tech), t=t_s, e=e_v, i_ua=i_ua, meta=meta, scan_labels=labels)

    if not curves:
        raise ValueError(f"{path.name}: no curves found in this Nova file")
    for k, c in enumerate(curves):
        c.uid = f"{path.name}#{k}"
    return DataFile(path=path, fmt="autolab", curves=curves)


register(Reader("autolab", "Metrohm Autolab Nova export (.txt, .csv)", (".txt", ".csv"), sniff, load, verified=False))
