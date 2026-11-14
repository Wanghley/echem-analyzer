"""Gamry Framework ``.DTA`` files (tab-separated text), preview.

A file is a list of ``KEY<TAB>TYPE<TAB>value<TAB>label`` header lines followed by ``NAME<TAB>TABLE<TAB>n<TAB>...`` blocks:
a column-name line, a unit line and ``n`` data rows, every one starting with a tab.  The ``TAG`` line names the technique.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import numpy as np

from .. import techniques
from ..models import Curve, DataFile
from . import Reader, register
from ._common import (current_scale, find_column, number, orient_impedance, read_text, rows_to_array, scans_from_segments,
                      sweep_meta)

TAGS = {
    "CV": techniques.CV, "LSV": techniques.LSV, "DPV": techniques.DPV, "SWV": techniques.SWV, "SQWV": techniques.SWV,
    "NPV": techniques.NPV, "CHRONOA": techniques.CA, "CA": techniques.CA, "CHRONOC": techniques.CC,
    "CHRONOP": techniques.CP, "CORPOT": techniques.OCP, "OCP": techniques.OCP,
    "EISPOT": techniques.EIS, "EISGALV": techniques.EIS, "EISMON": techniques.EIS,
}
_TABLE = re.compile(r"^[A-Z]*CURVE\d*$")


def sniff(head: str) -> bool:
    return bool(re.match(r"\s*EXPLAIN\b", head.lstrip("﻿"))) and bool(re.search(r"(?m)^TAG\t", head))


def _split(line: str) -> list[str]:
    return [c.strip() for c in line.rstrip("\r\n").split("\t")]


def _read(text: str):
    """(header, tables): header maps KEY -> (value, label); a table is (name, columns, units, rows)."""
    lines = text.splitlines()
    header: dict[str, tuple[str, str]] = {}
    tables: list[tuple[str, list[str], list[str], list[list[str]]]] = []
    i = 0
    while i < len(lines):
        parts = _split(lines[i])
        if len(parts) >= 3 and parts[1] == "TABLE" and _TABLE.match(parts[0]) and i + 2 < len(lines):
            cols, units = _split(lines[i + 1])[1:], _split(lines[i + 2])[1:]
            rows: list[list[str]] = []
            i += 3
            while i < len(lines) and lines[i].startswith("\t"):
                rows.append(_split(lines[i])[1:])
                i += 1
            tables.append((parts[0], cols, units, rows))
            continue
        if len(parts) >= 3 and parts[0] and parts[1] in ("QUANT", "LABEL", "PSTAT", "SELECTOR", "TOGGLE", "POTEN"):
            header.setdefault(parts[0], (parts[2], parts[3] if len(parts) > 3 else ""))
        elif parts and parts[0] == "TAG" and len(parts) > 1:
            header["TAG"] = (parts[1], "")
        i += 1
    return header, tables


def _quantity(header: dict, key: str) -> Optional[float]:
    if key not in header:
        return None
    v = number(header[key][0])
    return None if v != v else v


def _scan_rate(header: dict) -> Optional[float]:
    v = _quantity(header, "SCANRATE")
    if v is None:
        return None
    return v / 1000.0 if "mv/s" in header["SCANRATE"][1].lower() else v            # the label states the unit


def _technique(header: dict, title: str) -> str:
    tag = header.get("TAG", ("", ""))[0].strip().upper()
    return TAGS.get(tag) or techniques.classify(title)


def _unit_token(units: list[str], i: Optional[int]) -> str:
    return units[i].split()[0] if i is not None and i < len(units) and units[i].strip() else ""


def _table_curves(path: Path, tech: str, name: str, cols: list[str], units: list[str], rows, header: dict,
                  title: str) -> list[Curve]:
    data = rows_to_array(rows, len(cols))
    if data.shape[0] < 2:
        return []
    meta = {"run_title": title, "measurement_index": 0, "method_id": tech.lower(), "device": header.get("PSTAT", ("", ""))[0],
            "timestamp": f"{header.get('DATE', ('', ''))[0]} {header.get('TIME', ('', ''))[0]}".strip()}

    def col(*names: str) -> Optional[int]:
        return find_column(cols, *[rf"^{re.escape(n)}$" for n in names])

    def make(kind, ctitle, x, y, x_unit, y_unit, **kw) -> Curve:
        return Curve(kind=kind, title=ctitle, x=x, y=y, x_unit=x_unit, y_unit=y_unit, source=str(path), **kw)

    if tech == techniques.EIS:
        f, zr, zi = col("Freq"), col("Zreal"), col("Zimag")
        if None in (f, zr, zi):
            return []
        freq, zre, zim = orient_impedance(data[:, f], data[:, zr], data[:, zi])
        if freq.size < 2:
            return []
        return [make(techniques.EIS, "Impedance", zre, -zim, "Ω", "Ω", meta={**meta, "method_id": "eis"},
                     extra={"freq": freq, "zre": zre, "zim": zim})]

    t_i = col("T")
    sweep = techniques.is_voltammetric(tech)
    pulse = tech in (techniques.DPV, techniques.SWV, techniques.NPV)
    x_i = col("Vf", "Vstep", "Vfwd", "Vu") if sweep else t_i
    y_i = (col("Idif", "Im", "Ifwd", "I") if pulse else col("Im", "I", "Idif")) if tech not in (techniques.OCP, techniques.CP) \
        else col("Vf", "Vm", "Vu")
    if x_i is None or y_i is None:
        return []
    x, y = data[:, x_i], data[:, y_i]
    keep = np.isfinite(x) & np.isfinite(y)
    x, y, t = x[keep], y[keep], (data[keep, t_i] if t_i is not None else None)
    if x.size < 2:
        return []

    y_unit = "V" if tech in (techniques.OCP, techniques.CP) else "µA"
    if y_unit == "µA":
        scale = current_scale(_unit_token(units, y_i))
        y = y * (scale if scale is not None else 1e6)
    x_unit = "V" if sweep else "s"
    short = techniques.short(tech)

    if tech == techniques.CV:
        rate = _scan_rate(header)
        base = {**meta, **{k: v for k, v in (("e_begin", _quantity(header, "VINIT")), ("e_vtx1", _quantity(header, "VLIM1")),
                                             ("e_vtx2", _quantity(header, "VLIM2"))) if v is not None}}
        cycles = int(_quantity(header, "CYCLES") or 1)
        digits = re.search(r"\d+$", name)
        if digits is None and cycles > 1:                       # all cycles in one table: cut it at the vertices
            return [make(techniques.CV, f"CV i vs E Scan {k}", x[a:b], y[a:b], "V", y_unit, scan=k,
                         meta={**base, **sweep_meta(t[a:b] if t is not None else None, x[a:b], rate)})
                    for k, (a, b) in enumerate(scans_from_segments(x), start=1)]
        scan = int(digits.group()) if digits else 1
        return [make(techniques.CV, f"CV i vs E Scan {scan}", x, y, "V", y_unit, scan=scan,
                     meta={**base, **sweep_meta(t, x, rate)})]

    if tech == techniques.CA:
        dt = float(np.median(np.diff(x))) if x.size > 1 else 0.0
        e_step = _quantity(header, "VSTEP1")
        m = {**meta, "dt": dt, **({"E": e_step} if e_step is not None else {})}
        return [make(tech, "CA i vs t", x, y, x_unit, y_unit, meta=m)]
    ytag = "E" if y_unit == "V" else "i"
    return [make(tech, f"{short} {ytag} vs {'t' if not sweep else 'E'}", x, y, x_unit, y_unit, meta=meta)]


def load(path: str | Path) -> DataFile:
    path = Path(path)
    header, tables = _read(read_text(path))
    title = header.get("TITLE", ("", ""))[0] or ""
    tech = _technique(header, title)
    title = title or techniques.label(tech)
    curves: list[Curve] = []
    for name, cols, units, rows in tables:
        curves += _table_curves(path, tech, name, cols, units, rows, header, title)
    for k, c in enumerate(curves):
        c.uid = f"{path.name}#{k}"
    if not curves:
        raise ValueError(f"{path.name}: no curves found in this Gamry file")
    stamp = f"{header.get('DATE', ('', ''))[0]} {header.get('TIME', ('', ''))[0]}".strip()
    return DataFile(path=path, fmt="gamry", curves=curves, exported_at=stamp)


register(Reader("gamry", "Gamry Framework (.DTA)", (".dta",), sniff, load, verified=False))
