"""Helpers shared by the instrument readers (no Qt).

Vendor exports differ in header layout, delimiter, decimal mark and units, but they all end up as the same
:class:`~echem_analyzer.models.Curve`: currents in µA, potentials in V, time in s, impedance in Ω.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np

from .. import techniques
from ..io_palmsens import decode_text
from ..models import Curve

HEAD_CHARS = 8192                  # how much of a file the readers look at to recognise it

CURRENT_TO_UA = {"A": 1e6, "mA": 1e3, "µA": 1.0, "μA": 1.0, "uA": 1.0, "nA": 1e-3, "pA": 1e-6}


class UnsupportedFile(ValueError):
    """No reader recognises the file.  The caller may still offer the Import table for it."""


def read_text(path: str | Path) -> str:
    return decode_text(Path(path).read_bytes())


def read_head(path: str | Path, chars: int = HEAD_CHARS) -> str:
    """The start of a file as text, tolerant of a multi-byte character cut in two (used to recognise a format)."""
    with open(path, "rb") as fh:
        raw = fh.read(chars * 2 + 4)
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw[: len(raw) // 2 * 2].decode("utf-16", errors="replace")
    elif raw.startswith(b"\xef\xbb\xbf"):
        text = raw.decode("utf-8-sig", errors="replace")
    elif raw[:200].count(b"\x00") > 50:                              # UTF-16 without a byte-order mark
        text = raw[: len(raw) // 2 * 2].decode("utf-16-le", errors="replace")
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
    return text[:chars]


def number(cell: str) -> float:
    """A float from a cell, accepting a decimal comma ("0,4991"); NaN when it is blank or not a number."""
    c = cell.strip().strip('"').strip()
    if not c:
        return float("nan")
    try:
        return float(c)
    except ValueError:
        pass
    if "," in c:
        fixed = c.replace(",", "") if "." in c else c.replace(",", ".")
        try:
            return float(fixed)
        except ValueError:
            pass
    return float("nan")


def is_number(cell: str) -> bool:
    return bool(cell.strip()) and number(cell) == number(cell)


def rows_to_array(rows: Iterable[Sequence[str]], ncols: Optional[int] = None) -> np.ndarray:
    """Cells -> an (n, ncols) float array; short rows are padded with NaN."""
    rows = [list(r) for r in rows]
    if not rows:
        return np.empty((0, ncols or 0))
    width = ncols or max(len(r) for r in rows)
    out = np.full((len(rows), width), np.nan)
    for i, r in enumerate(rows):
        for j, cell in enumerate(r[:width]):
            out[i, j] = number(cell)
    return out


def current_scale(unit: str) -> Optional[float]:
    """Factor that turns a current in ``unit`` ("A", "mA", ...) into µA; None for anything else."""
    return CURRENT_TO_UA.get(unit.strip())


def unit_of(header: str) -> str:
    """'<I>/mA' -> 'mA';  'Time (s)' -> 's';  'WE(1).Current (A)' -> 'A';  'Re(Z)/Ohm' -> 'Ohm'."""
    h = header.strip()
    m = re.search(r"\(([^()]*)\)\s*$", h)
    if m:
        return m.group(1).strip()
    if "/" in h:
        return h.rsplit("/", 1)[1].strip()
    return ""


def find_column(names: Sequence[str], *patterns: str) -> Optional[int]:
    """Index of the first column whose name matches the first pattern that matches any (case-insensitive regex)."""
    for pat in patterns:
        rx = re.compile(pat, re.I)
        for i, n in enumerate(names):
            if rx.search(n):
                return i
    return None


def direction_segments(x: np.ndarray) -> list[tuple[int, int]]:
    """(start, stop) sample ranges, stop exclusive, of the monotone stretches of a swept signal.

    A reversal sample belongs to both neighbours.  Used to split a CV that an instrument wrote as one long trace.
    """
    x = np.asarray(x, float)
    if len(x) < 3:
        return [(0, len(x))]
    sign = np.sign(np.diff(x))
    for i in range(1, len(sign)):                    # a flat step keeps the previous direction
        if sign[i] == 0:
            sign[i] = sign[i - 1]
    if sign[0] == 0:
        nz = np.flatnonzero(sign)
        sign[: nz[0]] = sign[nz[0]] if len(nz) else 1
    turns = np.flatnonzero(sign[1:] != sign[:-1]) + 1                      # first sample of the new direction
    edges = [0, *[int(t) for t in turns], len(x) - 1]
    return [(a, b + 1) for a, b in zip(edges[:-1], edges[1:]) if b > a]


def scans_from_segments(x: np.ndarray, per_scan: int = 2) -> list[tuple[int, int]]:
    """Group the monotone stretches into scans of ``per_scan`` stretches (forward + back = one cycle)."""
    segs = direction_segments(x)
    if len(segs) <= per_scan:
        return [(0, len(x))]
    out = []
    for k in range(0, len(segs), per_scan):
        chunk = segs[k:k + per_scan]
        out.append((chunk[0][0], chunk[-1][1]))
    return out


def estimate_scan_rate(t: np.ndarray, e: np.ndarray) -> Optional[float]:
    """Scan rate in V/s from a time-stamped potential sweep (the median |dE/dt|); None when it cannot be told."""
    t, e = np.asarray(t, float), np.asarray(e, float)
    n = min(len(t), len(e))
    if n < 4:
        return None
    dt, de = np.diff(t[:n]), np.diff(e[:n])
    ok = (dt > 0) & np.isfinite(dt) & np.isfinite(de)
    if ok.sum() < 3:
        return None
    rates = np.abs(de[ok] / dt[ok])
    rates = rates[rates > 0]
    return float(np.median(rates)) if rates.size else None


def sweep_meta(t: Optional[np.ndarray], e: np.ndarray, declared: Optional[float]) -> dict:
    """Scan-rate entries for ``Curve.meta``: the value the file states, else one estimated from time and potential."""
    if declared and declared > 0:
        return {"scan_rate": float(declared)}
    est = estimate_scan_rate(t, e) if t is not None else None
    return {"scan_rate": est, "scan_rate_source": "estimated from the time and potential columns"} if est else {}


def orient_impedance(freq: np.ndarray, zre: np.ndarray, zim: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Order the points by falling frequency and make Im(Z) negative for a capacitive spectrum (the app's convention)."""
    freq, zre, zim = (np.asarray(a, float) for a in (freq, zre, zim))
    keep = np.isfinite(freq) & np.isfinite(zre) & np.isfinite(zim) & (freq > 0)
    freq, zre, zim = freq[keep], zre[keep], zim[keep]
    if zim.size and np.mean(zim) > 0:
        zim = -zim
    order = np.argsort(-freq, kind="stable")
    return freq[order], zre[order], zim[order]


# --------------------------------------------------------------------------- arrays -> curves

def impedance_curve(path: Path, title: str, freq, zre, zim, meta: dict) -> Optional[Curve]:
    """A Nyquist curve (x = Z′, y = −Z″) carrying freq / zre / zim in ``extra``; None when fewer than two usable points."""
    f, zr, zi = orient_impedance(freq, zre, zim)
    if f.size < 2:
        return None
    return Curve(kind=techniques.EIS, title="Impedance", x=zr, y=-zi, x_unit="Ω", y_unit="Ω", source=str(path),
                 meta={**meta, "run_title": title, "method_id": "eis"}, extra={"freq": f, "zre": zr, "zim": zi})


def build_curves(path: Path, tech: str, title: str, *, t=None, e=None, i_ua=None, meta: Optional[dict] = None,
                 scan: Optional[int] = None, scan_labels=None, rate: Optional[float] = None) -> list[Curve]:
    """The curve(s) of one non-impedance measurement from its columns (time s, potential V, current µA).

    * potentiometry (OCP, CP): potential against time;
    * voltammetry: current against potential, a CV cut into one curve per scan;
    * everything else (CA, CC, ...): current against time.

    A CV is split by ``scan`` (this table is that one scan), else by ``scan_labels`` (a cycle number per sample), else at the
    reversal points of the potential.  ``rate`` is the scan rate in V/s the file states; without it one is estimated.
    """
    arrays = {"t": t, "e": e, "i": i_ua}
    arrays = {k: (None if v is None else np.asarray(v, float)) for k, v in arrays.items()}
    if tech in (techniques.OCP, techniques.CP):
        use, x_key, y_key, y_unit = ("t", "e"), "t", "e", "V"
    elif techniques.is_voltammetric(tech):
        use, x_key, y_key, y_unit = ("e", "i"), "e", "i", "µA"
    else:
        use, x_key, y_key, y_unit = ("t", "i"), "t", "i", "µA"
    if any(arrays[k] is None for k in use):
        return []
    n = min(len(v) for v in arrays.values() if v is not None)
    arrays = {k: (None if v is None else v[:n]) for k, v in arrays.items()}
    labels = None if scan_labels is None else np.asarray(scan_labels)[:n]
    keep = np.isfinite(arrays[x_key]) & np.isfinite(arrays[y_key])
    arrays = {k: (None if v is None else v[keep]) for k, v in arrays.items()}
    labels = None if labels is None else labels[keep]
    x, y = arrays[x_key], arrays[y_key]
    if x.size < 2:
        return []

    base = {"run_title": title, "measurement_index": 0, "method_id": tech.lower(), **(meta or {})}
    short = techniques.short(tech)
    x_unit = "s" if x_key == "t" else "V"

    def one(a: int, b: int, ctitle: str, scan_no: Optional[int] = None, extra_meta: Optional[dict] = None) -> Curve:
        return Curve(kind=tech, title=ctitle, x=x[a:b], y=y[a:b], x_unit=x_unit, y_unit=y_unit, scan=scan_no, source=str(path),
                     meta={**base, **(extra_meta or {})})

    if tech == techniques.CV:
        if scan is not None:
            ranges = [(0, x.size, scan)]
        elif labels is not None and np.unique(labels).size > 1:
            order = list(dict.fromkeys(labels.tolist()))
            ranges = []
            for k, lab in enumerate(order, start=1):
                idx = np.flatnonzero(labels == lab)
                ranges.append((int(idx[0]), int(idx[-1]) + 1, k))
        else:
            ranges = [(a, b, k) for k, (a, b) in enumerate(scans_from_segments(x), start=1)]
        t_all = arrays["t"]
        return [one(a, b, f"CV i vs E Scan {k}", k, sweep_meta(None if t_all is None else t_all[a:b], x[a:b], rate))
                for a, b, k in ranges if b - a >= 2]
    if tech == techniques.CA or (tech == techniques.OTHER and x_key == "t"):
        c = one(0, x.size, "CA i vs t" if tech == techniques.CA else "i vs t", None, {"dt": float(np.median(np.diff(x)))})
        return [c]
    if tech in (techniques.OCP, techniques.CP):
        return [one(0, x.size, f"{short} E vs t")]
    return [one(0, x.size, f"{short} {'i' if y_key == 'i' else 'y'} vs {'E' if x_key == 'e' else 't'}")]
