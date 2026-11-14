"""BioLogic EC-Lab ``.mpt`` files (tab-separated text), preview.

The first line is ``EC-Lab ASCII FILE`` and the second says how many header lines there are; the column names
(``time/s``, ``Ewe/V``, ``<I>/mA``, ``cycle number``, ``freq/Hz``, ``Re(Z)/Ohm``, ``-Im(Z)/Ohm``) are on the last of them.
EC-Lab writes the technique's name in the settings block.  The decimal mark follows the computer's language.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import numpy as np

from .. import techniques
from ..models import DataFile
from . import Reader, register
from ._common import (build_curves, current_scale, find_column, impedance_curve, read_text, rows_to_array, unit_of, number)

# EC-Lab technique names, most specific first.  techniques.classify() would call "Chronoamperometry / Chronocoulometry"
# a chronocoulometry; in EC-Lab that is the name of the (amperometric) technique.
_NAMES = (
    (r"impedance|\bpeis\b|\bgeis\b", techniques.EIS),
    (r"open circuit voltage|\bocv\b", techniques.OCP),
    (r"chronopotentiometry", techniques.CP),
    (r"chronoamperometry", techniques.CA),
    (r"chronocoulometry", techniques.CC),
    (r"differential pulse", techniques.DPV),
    (r"square wave", techniques.SWV),
    (r"normal pulse", techniques.NPV),
    (r"linear sweep", techniques.LSV),
    (r"cyclic voltammetry", techniques.CV),
)
_NUM = r"(-?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?)"


def sniff(head: str) -> bool:
    return head.lstrip("﻿").startswith("EC-Lab ASCII FILE")


def _technique_line(header: list[str]) -> tuple[str, str]:
    """(technique key, the header line that names it); OTHER when no known name appears."""
    for pattern, key in _NAMES:
        for ln in header:
            if re.search(pattern, ln, re.I):
                return key, ln.strip().split("\t")[0].strip()
    return techniques.OTHER, ""


def _column_line(lines: list[str], declared: int) -> int:
    """Index of the line holding the column names.  The declared count is trusted, give or take one."""
    def looks_like_columns(k: int) -> bool:
        return 0 <= k < len(lines) and "\t" in lines[k] and re.search(r"time/s|freq/Hz|Ewe/V|<I>/", lines[k]) is not None

    for k in (declared - 1, declared - 2, declared):
        if looks_like_columns(k):
            return k
    for k in range(min(len(lines), 200)):
        if looks_like_columns(k):
            return k
    raise ValueError("no column line (time/s, Ewe/V, <I>/mA ...) in this EC-Lab file")


def _setting(text: str, label: str) -> Optional[float]:
    m = re.search(rf"(?im)^\s*{label}\s*(?:\(V\))?\s*[:\t ]\s*{_NUM}", text)
    return None if m is None else number(m.group(1))


def _scan_rate(text: str) -> Optional[float]:
    """Scan rate in V/s from a "Scan rate (mV/s)  100.000" settings line."""
    m = re.search(rf"(?i)scan rate\s*\(?\s*(mV/s|V/s|µV/s|uV/s)?\s*\)?[\s:=]*{_NUM}", text)
    if m is None:
        return None
    value = number(m.group(2))
    factor = {"mv/s": 1e-3, "v/s": 1.0, "µv/s": 1e-6, "uv/s": 1e-6}.get((m.group(1) or "mv/s").lower(), 1e-3)
    return value * factor if value == value else None


def load(path: str | Path) -> DataFile:
    path = Path(path)
    lines = read_text(path).splitlines()
    m = re.search(r"Nb header lines\s*:\s*(\d+)", "\n".join(lines[:5]))
    at = _column_line(lines, int(m.group(1)) if m else 0)
    header_lines, names = lines[:at], [c.strip() for c in lines[at].split("\t")]
    header = "\n".join(header_lines)
    data = rows_to_array((ln.split("\t") for ln in lines[at + 1:] if ln.strip()), len(names))
    data = data[np.isfinite(data).any(axis=1)] if data.size else data
    if data.shape[0] < 2:
        raise ValueError(f"{path.name}: no data rows in this EC-Lab file")

    tech, title = _technique_line(header_lines)
    f_i = find_column(names, r"^freq/")
    e_i = find_column(names, r"^<?Ewe>?/", r"^<?Ece>?/")
    i_i = find_column(names, r"^<?I>?/")
    t_i = find_column(names, r"^time/")
    c_i = find_column(names, r"^cycle number")
    if tech == techniques.OTHER:                                   # no known name: decide from the columns
        if f_i is not None:
            tech = techniques.EIS
        elif e_i is not None and i_i is not None and c_i is not None:
            tech = techniques.CV
        elif e_i is not None and i_i is None:
            tech = techniques.OCP
        elif i_i is not None:
            tech = techniques.CA
    title = title or techniques.label(tech)
    device = re.search(r"(?m)^Device\s*:\s*(.+)$", header)
    started = re.search(r"(?m)^Acquisition started on\s*:\s*(.+)$", header)
    meta = {"device": device.group(1).strip() if device else "", "timestamp": started.group(1).strip() if started else ""}
    labels = None if c_i is None else data[:, c_i]

    curves = []
    if tech == techniques.EIS:
        zr_i = find_column(names, r"^re\(z\)/")
        zi_i = find_column(names, r"^-?im\(z\)/")
        if None in (f_i, zr_i, zi_i):
            raise ValueError(f"{path.name}: an impedance file needs freq/Hz, Re(Z) and Im(Z) columns")
        zim = data[:, zi_i] * (-1.0 if names[zi_i].lstrip().startswith("-") else 1.0)        # "-Im(Z)" is positive when capacitive
        groups = [np.ones(len(data), bool)] if labels is None or np.unique(labels[np.isfinite(labels)]).size < 2 else \
            [labels == lab for lab in dict.fromkeys(labels[np.isfinite(labels)].tolist())]
        for g in groups:
            c = impedance_curve(path, title, data[g, f_i], data[g, zr_i], zim[g], meta)
            if c is not None:
                curves.append(c)
    else:
        e_v = None if e_i is None else data[:, e_i] * (1e-3 if unit_of(names[e_i]).lower() == "mv" else 1.0)
        scale = current_scale(unit_of(names[i_i])) if i_i is not None else None
        i_ua = None if i_i is None else data[:, i_i] * (scale if scale is not None else 1e3)       # EC-Lab's default is mA
        t_s = None if t_i is None else data[:, t_i]
        rate = None
        if tech == techniques.CV:
            rate = _scan_rate(header)
            for key, label in (("e_begin", "Ei"), ("e_vtx1", "E1"), ("e_vtx2", "E2")):
                v = _setting(header, label)
                if v is not None:
                    meta[key] = v
        elif tech == techniques.CA and e_v is not None:
            meta["E"] = float(np.nanmedian(e_v))
        curves = build_curves(path, tech, title, t=t_s, e=e_v, i_ua=i_ua, meta=meta, scan_labels=labels, rate=rate)

    if not curves:
        raise ValueError(f"{path.name}: no curves found in this EC-Lab file")
    for k, c in enumerate(curves):
        c.uid = f"{path.name}#{k}"
    return DataFile(path=path, fmt="biologic", curves=curves, exported_at=meta["timestamp"])


register(Reader("biologic", "BioLogic EC-Lab (.mpt)", (".mpt",), sniff, load, verified=False))
