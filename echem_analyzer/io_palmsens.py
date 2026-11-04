"""Readers for PalmSens PSTrace exports (``.csv`` and ``.pssession``)."""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from . import techniques
from .models import KIND_CA, KIND_CV, KIND_OTHER, Curve, DataFile

# --------------------------------------------------------------------------- helpers

_CURRENT_SCALE_TO_UA = {
    "Ampere": 1e6,
    "MilliAmpere": 1e3,
    "MicroAmpere": 1.0,
    "NanoAmpere": 1e-3,
    "PicoAmpere": 1e-6,
}
_UNIT_LABEL_TO_UA = {"A": 1e6, "mA": 1e3, "µA": 1.0, "uA": 1.0, "nA": 1e-3, "pA": 1e-6}

_STOCK_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(mM|µM|μM|uM|nM|M)(?![A-Za-z])")


def decode_text(raw: bytes) -> str:
    """Decode a PalmSens export (UTF-16 LE/BE with BOM, or UTF-8)."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    # PalmSens sometimes omits the BOM; UTF-16 text has a NUL in every other byte.
    sample = raw[:200]
    if sample and sample.count(b"\x00") > len(sample) // 4:
        return raw.decode("utf-16-le")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def parse_stock_from_title(title: str) -> Optional[tuple[float, str]]:
    """'std addition GY 505.1mM' -> (505.1, 'mM'); None if there is no concentration."""
    m = _STOCK_RE.search(title or "")
    if not m:
        return None
    unit = m.group(2).replace("μ", "µ").replace("uM", "µM")
    return float(m.group(1).replace(",", ".")), unit


def _classify(title: str, method_id: str = "") -> tuple[str, Optional[int]]:
    """(technique key, CV scan number) for a curve title and the instrument's method id."""
    m = re.search(r"scan\s*(\d+)", title.lower())
    return techniques.classify(title, method_id), (int(m.group(1)) if m else None)


def _to_float_array(cells: Iterable[str]) -> np.ndarray:
    out = []
    for c in cells:
        c = c.strip()
        out.append(float(c) if c else np.nan)
    return np.asarray(out, dtype=float)


# --------------------------------------------------------------------------- CSV

def load_csv(path: str | Path) -> DataFile:
    path = Path(path)
    text = decode_text(path.read_bytes())
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 6:
        raise ValueError(f"{path.name}: not a PalmSens CSV export (too few rows)")

    exported_at = rows[0][1].strip() if len(rows[0]) > 1 else ""

    # Locate the 'Date and time measurement:' row; titles sit directly above, units below.
    r_meas = next(
        (i for i, r in enumerate(rows[:15]) if r and r[0].strip().lower().startswith("date and time measurement")),
        4,
    )
    titles, stamps, units = rows[r_meas - 1], rows[r_meas], rows[r_meas + 1]
    data = [r for r in rows[r_meas + 2:] if any(c.strip() for c in r)]
    ncols = max((len(r) for r in data), default=0)

    curves: list[Curve] = []
    for col in range(0, len(titles), 2):
        full = titles[col].strip()
        if not full:
            continue
        if col + 1 >= max(ncols, len(units)):
            continue
        run_title, _, curve_title = full.partition(": ")
        if not curve_title:
            run_title, curve_title = "", full

        xs, ys = [], []
        for r in data:
            if col + 1 < len(r) and r[col].strip() and r[col + 1].strip():
                try:
                    xs.append(float(r[col]))
                    ys.append(float(r[col + 1]))
                except ValueError:
                    continue
        if not xs:
            continue

        x_unit = units[col].strip() if col < len(units) else ""
        y_unit = units[col + 1].strip() if col + 1 < len(units) else ""
        x, y = np.asarray(xs), np.asarray(ys)
        scale = _UNIT_LABEL_TO_UA.get(y_unit)
        if scale is not None:
            y, y_unit = y * scale, "µA"

        kind, scan = _classify(curve_title or full)
        stamp = stamps[col + 1].strip() if col + 1 < len(stamps) else ""
        meta = {"run_title": run_title, "timestamp": stamp}
        if kind == KIND_CA and len(x) > 1:
            meta["dt"] = float(np.median(np.diff(x)))
        curves.append(
            Curve(kind=kind, title=curve_title, x=x, y=y, x_unit=x_unit, y_unit=y_unit,
                  scan=scan, meta=meta, source=str(path), uid=f"{path.name}#{len(curves)}")
        )
    if not curves:
        raise ValueError(f"{path.name}: no curves found")
    return DataFile(path=path, fmt="csv", curves=curves, exported_at=exported_at)


# --------------------------------------------------------------------------- pssession

def _method_value(method: str, key: str) -> Optional[str]:
    m = re.search(rf"(?m)^{re.escape(key)}=([^\r\n]*)", method or "")
    return m.group(1).strip() if m else None


def _array_values(arr: dict) -> np.ndarray:
    return np.asarray([v["V"] for v in arr.get("DataValues", [])], dtype=float)


def _unit_scale_and_label(unit: dict) -> tuple[float, str]:
    """Return (multiplier, label). Currents are normalised to µA."""
    utype = (unit or {}).get("Type", "")
    short = utype.rsplit(".", 1)[-1]
    if (unit or {}).get("Q") == "Current" and short in _CURRENT_SCALE_TO_UA:
        return _CURRENT_SCALE_TO_UA[short], "µA"
    return 1.0, (unit or {}).get("S", "")


def _eis_curves(meas: dict, run_title: str, path: Path, start_index: int) -> list[Curve]:
    """Best-effort EIS extraction from a measurement's ``EISDataList``.

    The layout was not verifiable against a real file: arrays are recognised by the words in their
    description (frequency / Z' / Z''); if that fails nothing is returned and the user can use
    *Import table* instead.
    """
    out: list[Curve] = []
    for item in meas.get("EISDataList") or []:
        found: dict[str, np.ndarray] = {}
        for v in ((item.get("DataSet") or {}).get("Values") or []):
            desc = str(v.get("Description", "")).lower()
            vals = np.asarray([d["V"] for d in v.get("DataValues", []) if isinstance(d, dict) and "V" in d], float)
            if vals.size == 0:
                continue
            if re.search(r"freq", desc):
                found.setdefault("freq", vals)
            elif re.search(r"zim|z''|z″|imag|-z", desc):
                found.setdefault("zim", vals)
            elif re.search(r"zre|z'|z′|real", desc):
                found.setdefault("zre", vals)
        if {"freq", "zre", "zim"} <= found.keys() and len({a.size for a in found.values()}) == 1:
            zim = found["zim"]
            zim = -np.abs(zim) if np.mean(zim) > 0 else zim          # internal convention: Im(Z) < 0 when capacitive
            found["zim"] = zim
            out.append(Curve(
                kind=techniques.EIS, title=str(item.get("Title", "EIS")), x=found["zre"], y=-zim, x_unit="Ω",
                y_unit="Ω", meta={"run_title": run_title, "method_id": "eis", "measurement_index": meas.get("_mi")},
                source=str(path), uid=f"{path.name}#{start_index + len(out)}",
                extra={"freq": found["freq"], "zre": found["zre"], "zim": zim}))
    return out


def load_pssession(path: str | Path) -> DataFile:
    path = Path(path)
    text = decode_text(path.read_bytes()).lstrip("﻿")
    text = text[: text.rfind("}") + 1]          # drop trailing NULs / garbage
    doc = json.loads(text)

    curves: list[Curve] = []
    for mi, meas in enumerate(doc.get("Measurements", [])):
        method = meas.get("Method", "")
        method_id = (_method_value(method, "METHOD_ID") or "").lower()
        run_title = meas.get("Title", f"Measurement {mi + 1}")
        e_dc = _method_value(method, "E")
        t_int = _method_value(method, "T_INTERVAL")
        meas["_mi"] = mi
        eis = _eis_curves(meas, run_title, path, len(curves)) if meas.get("EISDataList") else []
        if eis:                                    # impedance parsed from the EIS data block
            curves.extend(eis)
            continue

        for ci, c in enumerate(meas.get("Curves") or []):
            xa, ya = c.get("XAxisDataArray"), c.get("YAxisDataArray")
            if not xa or not ya:
                continue
            x, y = _array_values(xa), _array_values(ya)
            n = min(len(x), len(y))
            if n == 0:
                continue
            x, y = x[:n], y[:n]
            xs, x_unit = _unit_scale_and_label(xa.get("Unit"))
            ys, y_unit = _unit_scale_and_label(ya.get("Unit"))
            x, y = x * xs, y * ys

            title = c.get("Title", f"curve {ci + 1}")
            kind, scan = _classify(title, method_id)
            if kind == KIND_CV and scan is None:
                scan = ci + 1

            meta = {
                "run_title": run_title,
                "measurement_index": mi,
                "timestamp": meas.get("UTCTimeStamp", meas.get("TimeStamp", "")),
                "device": meas.get("DeviceSerial", ""),
                "method_id": method_id,
            }
            if kind == KIND_CA:
                if e_dc is not None:
                    try:
                        meta["E"] = float(e_dc)
                    except ValueError:
                        pass
                if t_int is not None:
                    try:
                        meta["dt"] = float(t_int)
                    except ValueError:
                        pass
                if "dt" not in meta and len(x) > 1:
                    meta["dt"] = float(np.median(np.diff(x)))
            elif kind == KIND_CV:
                for key, name in (("SCAN_RATE", "scan_rate"), ("E_BEGIN", "e_begin"), ("E_VTX1", "e_vtx1"),
                                  ("E_VTX2", "e_vtx2"), ("E_STEP", "e_step")):
                    raw = _method_value(method, key)
                    if raw is not None:
                        try:
                            meta[name] = float(raw)
                        except ValueError:
                            pass
            curves.append(
                Curve(kind=kind, title=title, x=x, y=y, x_unit=x_unit, y_unit=y_unit,
                      scan=scan, meta=meta, source=str(path), uid=f"{path.name}#{len(curves)}")
            )
    if not curves:
        raise ValueError(f"{path.name}: no curves found")
    return DataFile(path=path, fmt="pssession", curves=curves)


# --------------------------------------------------------------------------- dispatcher

SUPPORTED_SUFFIXES = (".csv", ".pssession")


def load_any(path: str | Path) -> DataFile:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return load_csv(path)
    if suffix == ".pssession":
        return load_pssession(path)
    raise ValueError(f"Unsupported file type: {path.name}")


def find_data_files(folder: str | Path) -> list[Path]:
    folder = Path(folder)
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES and not p.name.startswith(("~$", "."))
    )
