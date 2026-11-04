"""Import any delimited table (CSV / TSV / text / Excel) as measurements (no Qt).

Other potentiostats and spreadsheets export plain tables.  ``read_table`` finds the delimiter, the header
and the units; ``build_curve`` turns chosen columns into a :class:`Curve` of a given technique (EIS needs
frequency, Z′ and Z″).
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from . import techniques
from .io_palmsens import decode_text
from .models import Curve

_UNIT_RE = re.compile(r"[\[(]([^\])]+)[\])]|/\s*([A-Za-zµμΩ°%]+)\s*$")
_NUM_RE = re.compile(r"^\s*[-+]?(\d+([.,]\d*)?|[.,]\d+)([eE][-+]?\d+)?\s*$")


@dataclass
class Table:
    path: str
    columns: list[str]
    units: list[str]
    data: np.ndarray                      # n_rows x n_cols floats (NaN where blank)
    delimiter: str = ","
    header_row: Optional[int] = None
    skipped: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def n_rows(self) -> int:
        return int(self.data.shape[0])


def split_header(text: str) -> tuple[str, str]:
    """'E (V)' -> ('E', 'V');  'i/µA' -> ('i', 'µA');  'Z' [Ohm]' -> ("Z'", 'Ohm')."""
    text = text.strip()
    m = _UNIT_RE.search(text)
    if not m:
        return text, ""
    unit = (m.group(1) or m.group(2) or "").strip()
    return text[: m.start()].strip(" _-/") or text, unit


def _is_num(cell: str) -> bool:
    return bool(_NUM_RE.match(cell)) if cell.strip() else False


def _to_float(cell: str, decimal: str) -> float:
    c = cell.strip()
    if not c:
        return float("nan")
    if decimal == ",":
        # "1.234,56" (thousands dot + decimal comma) or "1,5": either way the comma is the decimal mark
        c = c.replace(".", "").replace(",", ".") if "." in c and "," in c else c.replace(",", ".")
    try:
        return float(c)
    except ValueError:
        return float("nan")


def _sniff_delimiter(lines: list[str]) -> str:
    best, best_score = ",", -1.0
    for d in (",", ";", "\t", "|", None):
        counts = [len(ln.split(d)) if d else len(ln.split()) for ln in lines if ln.strip()]
        if not counts:
            continue
        mode = max(set(counts), key=counts.count)
        if mode < 2:
            continue
        score = counts.count(mode) / len(counts) + 0.01 * min(mode, 10)
        if score > best_score:
            best, best_score = (d or " "), score
    return best


def read_rows(path: Path) -> list[list[str]]:
    """Rows of cells from a text table or the first sheet of an .xlsx file."""
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        return [["" if v is None else str(v) for v in row] for row in ws.iter_rows(values_only=True)]
    text = decode_text(path.read_bytes())
    lines = [ln for ln in text.splitlines()]
    delim = _sniff_delimiter(lines[:60])
    if delim == " ":
        return [ln.split() for ln in lines]
    return list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delim))


def read_table(path: str | Path, skip_rows: Optional[int] = None) -> Table:
    """Parse a table: header = last mostly-text row before the numbers; units are read from header text."""
    path = Path(path)
    rows = read_rows(path)
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise ValueError(f"{path.name}: the file is empty")
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]

    def numeric_fraction(r: list[str]) -> float:
        cells = [c for c in r if c.strip()]
        return sum(_is_num(c) for c in cells) / len(cells) if cells else 0.0

    first = next((i for i, r in enumerate(rows) if numeric_fraction(r) >= 0.8 and sum(bool(c.strip()) for c in r) >= 2), None)
    if skip_rows is not None:
        first = skip_rows
    if first is None:
        raise ValueError(f"{path.name}: no numeric rows found")
    header_row = first - 1 if first > 0 and numeric_fraction(rows[first - 1]) < 0.5 else None
    names = rows[header_row] if header_row is not None else [f"col{j + 1}" for j in range(ncol)]
    comma_decimal = any("," in c and "." not in c and _NUM_RE.match(c) for r in rows[first:first + 20] for c in r)
    body = rows[first:]
    data = np.array([[_to_float(c, "," if comma_decimal else ".") for c in r] for r in body], float)
    keep = [j for j in range(ncol) if np.isfinite(data[:, j]).sum() >= max(2, 0.5 * len(data))]
    cols, units = [], []
    for j in keep:
        name, unit = split_header(names[j]) if names[j].strip() else (f"col{j + 1}", "")
        cols.append(name or f"col{j + 1}")
        units.append(unit)
    notes = []
    if comma_decimal:
        notes.append("decimal comma detected")
    return Table(str(path), cols, units, data[:, keep], _sniff_delimiter([",".join(r) for r in rows[:40]]), header_row,
                 first, notes)


_UNIT_SCALE = {"a": 1e6, "ma": 1e3, "µa": 1.0, "ua": 1.0, "μa": 1.0, "na": 1e-3, "pa": 1e-6,
               "mv": 1e-3, "v": 1.0, "µv": 1e-6, "uv": 1e-6,
               "ms": 1e-3, "s": 1.0, "min": 60.0, "h": 3600.0,
               "khz": 1e3, "hz": 1.0, "mhz": 1e-3,
               "kohm": 1e3, "kω": 1e3, "ohm": 1.0, "Ω": 1.0, "mohm": 1e-3, "mω": 1e-3, "ω": 1.0}


def guess_roles(table: Table, technique: str) -> dict[str, Optional[int]]:
    """Heuristic column choice (indices) for the chosen technique; the dialog lets the user change it."""
    low = [c.lower() for c in table.columns]

    def find(*pats: str) -> Optional[int]:
        for j, c in enumerate(low):
            if any(re.search(p, c) for p in pats):
                return j
        return None

    if technique == techniques.EIS:
        return {"freq": find(r"freq", r"^f$", r"hz"), "zre": find(r"z'|z′|zre|^re", r"real"),
                "zim": find(r"z''|z″|zim|^im", r"imag", r"-z")}
    if techniques.family(technique) == "voltammetry":
        return {"x": find(r"^e", r"pot", r"volt", r"^v$"), "y": find(r"^i", r"curr")}
    if technique == techniques.CC:
        return {"x": find(r"^t", r"time"), "y": find(r"^q", r"charge")}
    if techniques.family(technique) in ("amperometry",):
        return {"x": find(r"^t", r"time"), "y": find(r"^i", r"curr")}
    if techniques.family(technique) == "potentiometry":
        return {"x": find(r"^t", r"time"), "y": find(r"^e", r"pot", r"^v$")}
    return {"x": 0 if table.columns else None, "y": 1 if len(table.columns) > 1 else None}


def convert_units(values: np.ndarray, unit: str, target: str) -> tuple[np.ndarray, str]:
    """Scale to µA / V / s / Hz / Ω when the unit is recognised; otherwise leave unchanged."""
    u = unit.strip().lower().replace("μ", "µ")
    if target == "µA" and u in ("a", "ma", "µa", "ua", "na", "pa"):
        return values * _UNIT_SCALE[u], "µA"
    if target == "V" and u in ("mv", "v", "µv", "uv"):
        return values * _UNIT_SCALE[u], "V"
    if target == "s" and u in ("ms", "s", "min", "h"):
        return values * _UNIT_SCALE[u], "s"
    if target == "Hz" and u in ("khz", "hz", "mhz"):
        return values * _UNIT_SCALE[u], "Hz"
    if target == "Ω" and u in ("kohm", "kω", "ohm", "ω", "mohm", "mω", "Ω".lower()):
        return values * _UNIT_SCALE[u], "Ω"
    return values, unit


def build_curve(table: Table, technique: str, roles: dict[str, Optional[int]], title: str,
                eis_zim_negated: bool = True, uid: str = "") -> Curve:
    """Make a Curve from the chosen columns; EIS needs ``freq``, ``zre`` and ``zim``."""
    def col(j: Optional[int], target: str) -> tuple[np.ndarray, str]:
        if j is None:
            raise ValueError("a required column is not assigned")
        return convert_units(table.data[:, j], table.units[j], target)

    meta = {"run_title": title, "method_id": technique.lower(), "imported": True}
    if technique == techniques.EIS:
        f, _ = col(roles.get("freq"), "Hz")
        zre, _ = col(roles.get("zre"), "Ω")
        zim, _ = col(roles.get("zim"), "Ω")
        ok = np.isfinite(f) & np.isfinite(zre) & np.isfinite(zim)
        f, zre, zim = f[ok], zre[ok], zim[ok]
        if eis_zim_negated:                                 # the file lists −Z″ (positive): internal Im(Z) is negative
            zim = -np.abs(zim) if np.mean(zim) > 0 else zim
        order = np.argsort(f)[::-1]                          # high -> low frequency
        f, zre, zim = f[order], zre[order], zim[order]
        return Curve(kind=techniques.EIS, title=title, x=zre, y=-zim, x_unit="Ω", y_unit="Ω", meta=meta,
                     source=table.path, uid=uid, extra={"freq": f, "zre": zre, "zim": zim})
    tgt_x = {"voltammetry": "V", "amperometry": "s", "potentiometry": "s"}.get(techniques.family(technique), "")
    tgt_y = {techniques.CC: "µC"}.get(technique) or {"voltammetry": "µA", "amperometry": "µA", "potentiometry": "V"}.get(
        techniques.family(technique), "")
    x, x_unit = col(roles.get("x"), tgt_x)
    y, y_unit = col(roles.get("y"), tgt_y)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 3:
        raise ValueError("fewer than 3 valid points in the chosen columns")
    return Curve(kind=technique, title=title, x=x[ok], y=y[ok], x_unit=x_unit or "", y_unit=y_unit or "",
                 meta=meta, source=table.path, uid=uid)
