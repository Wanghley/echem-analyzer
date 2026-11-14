"""Gamry Framework ``.DTA`` files (tab-separated text), preview.

A file is a list of ``KEY<TAB>TYPE<TAB>value<TAB>label`` header lines followed by ``NAME<TAB>TABLE<TAB>n<TAB>...`` blocks:
a column-name line, a unit line and ``n`` data rows, every one starting with a tab.  The ``TAG`` line names the technique.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .. import techniques
from ..models import Curve, DataFile
from . import Reader, register
from ._common import build_curves, current_scale, find_column, impedance_curve, number, read_text, rows_to_array

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
    meta = {"device": header.get("PSTAT", ("", ""))[0],
            "timestamp": f"{header.get('DATE', ('', ''))[0]} {header.get('TIME', ('', ''))[0]}".strip()}

    def col(*names: str) -> Optional[int]:
        return find_column(cols, *[rf"^{re.escape(n)}$" for n in names])

    if tech == techniques.EIS:
        f, zr, zi = col("Freq"), col("Zreal"), col("Zimag")
        if None in (f, zr, zi):
            return []
        c = impedance_curve(path, title, data[:, f], data[:, zr], data[:, zi], meta)
        return [c] if c else []

    def column(i: Optional[int]):
        return None if i is None else data[:, i]

    pulse = tech in (techniques.DPV, techniques.SWV, techniques.NPV)
    potentiometric = tech in (techniques.OCP, techniques.CP)
    i_idx = None if potentiometric else (col("Idif", "Im", "Ifwd", "I") if pulse else col("Im", "I", "Idif"))
    e_idx = col("Vf", "Vm", "Vu") if potentiometric else col("Vf", "Vstep", "Vfwd", "Vu")
    scale = current_scale(_unit_token(units, i_idx)) if i_idx is not None else None
    i_ua = None if i_idx is None else data[:, i_idx] * (scale if scale is not None else 1e6)

    rate = None
    if tech == techniques.CV:
        rate = _scan_rate(header)
        for key, label in (("VINIT", "e_begin"), ("VLIM1", "e_vtx1"), ("VLIM2", "e_vtx2")):
            v = _quantity(header, key)
            if v is not None:
                meta[label] = v
    if tech == techniques.CA:
        v = _quantity(header, "VSTEP1")
        if v is not None:
            meta["E"] = v
    digits = re.search(r"\d+$", name)
    one_table_of_cycles = tech == techniques.CV and digits is None and (_quantity(header, "CYCLES") or 1) > 1
    scan = int(digits.group()) if (tech == techniques.CV and digits) else (None if one_table_of_cycles else 1)
    return build_curves(path, tech, title, t=column(col("T")), e=column(e_idx), i_ua=i_ua, meta=meta,
                        scan=scan if tech == techniques.CV else None, rate=rate)


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
