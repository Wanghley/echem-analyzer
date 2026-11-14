"""Synthetic measurement files in the PalmSens layouts (for techniques without real example files)."""
import json
from pathlib import Path

import numpy as np

from echem_analyzer import eis


def gauss(e, e0, sigma, h):
    return h * np.exp(-0.5 * ((e - e0) / sigma) ** 2)


def dpv_trace(conc_uM: float, e=None, slope=2.0):
    """A DPV scan whose peak height is linear in concentration (0.8 µA per µM) on a sloping baseline."""
    e = np.linspace(-0.2, 0.6, 321) if e is None else e
    return e, 0.8 * conc_uM * np.exp(-0.5 * ((e - 0.2) / 0.03) ** 2) + slope * e + 0.5


def randles_spectrum(rct=1000.0, rs=100.0, q=2e-6, n=0.9, npts=50, noise=0.0, seed=1):
    f = np.logspace(5, -1, npts)
    z = eis.impedance("randles", dict(Rs=rs, Rct=rct, Q=q, n=n), f)
    if noise:
        z = z * (1 + np.random.default_rng(seed).normal(0, noise, f.size))
    return f, z


def write_pstrace_csv(path: Path, curves: list[tuple[str, np.ndarray, np.ndarray, str, str]]) -> None:
    """curves: (full title 'Technique: curve', x, y, x_unit, y_unit) in the UTF-16 PSTrace CSV layout."""
    n = max(len(c[1]) for c in curves)
    ncol = 2 * len(curves)
    row = lambda cells: ",".join(cells)                                        # noqa: E731
    lines = ["Date and time:,2026-11-03 10:00:00", "Notes:", "," * (ncol - 1)]
    lines.append(row(sum(([c[0], ""] for c in curves), [])))
    lines.append(row(sum((["Date and time measurement:", "2026-11-03 09:00:00"] for _ in curves), [])))
    lines.append(row(sum(([c[3], c[4]] for c in curves), [])))
    for k in range(n):
        lines.append(row(sum(([repr(float(c[1][k])), repr(float(c[2][k]))] if k < len(c[1]) else ["", ""] for c in curves), [])))
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-16"))


def _arr(values, unit_type, symbol, quantity):
    return {"Type": "x", "ArrayType": 0, "Description": symbol, "DataValueType": "x",
            "Unit": {"Type": unit_type, "S": symbol, "Q": quantity, "A": symbol},
            "DataValues": [{"V": float(v)} for v in values]}


def write_pssession(path: Path, measurements: list[dict]) -> None:
    """measurements: {title, method_id, curves: [(title, x, y, x_unit_type, y_unit_type)], eis: (f, z) | None}."""
    out = []
    for m in measurements:
        curves = [{"Title": t, "XAxisDataArray": _arr(x, xu, "x", "Potential" if "Volt" in xu else "Time"),
                   "YAxisDataArray": _arr(y, yu, "i", "Current")} for t, x, y, xu, yu in m.get("curves", [])]
        meas = {"Title": m["title"], "Method": f"METHOD_ID={m['method_id']}\r\nE=0\r\n" + "".join(f"{k}={v}\r\n" for k, v in m.get("method", {}).items()), "Curves": curves,
                "DataSet": {"Values": []}, "UTCTimeStamp": "639292523478891710"}
        if m.get("eis"):
            f, z = m["eis"]
            meas["EISDataList"] = [{"Title": "Impedance", "DataSet": {"Values": [
                {"Description": "freq", "DataValues": [{"V": float(v)} for v in f]},
                {"Description": "zre", "DataValues": [{"V": float(v)} for v in z.real]},
                {"Description": "zim", "DataValues": [{"V": float(v)} for v in z.imag]}]}}]
        out.append(meas)
    path.write_bytes(json.dumps({"Type": "PalmSens.DataFiles.SessionFile", "Measurements": out}).encode("utf-16"))


def cv_scan(nu: float, ip_slope: float, e0=0.2, sep=0.07):
    """One CV cycle whose anodic / cathodic peak heights are ip_slope·√ν (a diffusion-controlled couple)."""
    e_up = np.arange(-0.2, 0.6, 0.005)
    e = np.r_[e_up, e_up[::-1]]
    ip = ip_slope * np.sqrt(nu)
    up = np.r_[ip * np.exp(-0.5 * ((e_up - (e0 + sep / 2)) / 0.04) ** 2), np.zeros_like(e_up)]
    down = np.r_[np.zeros_like(e_up), -ip * np.exp(-0.5 * ((e_up[::-1] - (e0 - sep / 2)) / 0.04) ** 2)]
    return e, up + down


# --------------------------------------------------------------------------- other instruments
# Written from the vendors' file descriptions, not copied from real instrument files: they prove the readers agree with
# the layout described, not that every firmware writes exactly this.

def write_gamry_dta(path: Path, tag: str, title: str, quants: dict | None = None, tables=(), decimal: str = ".") -> None:
    """A Gamry Framework .DTA: header lines, then (NAME, columns, units, rows) tables whose lines start with a tab.

    quants maps a header key to (value, label)."""
    num = lambda v: (f"{v:.6E}" if isinstance(v, float) else str(v)).replace(".", decimal)               # noqa: E731
    lines = ["EXPLAIN\t", f"TAG\t{tag}", f"TITLE\tLABEL\t{title}\tTest &Identifier", "DATE\tLABEL\t11/5/2026\tDate",
             "TIME\tLABEL\t10:30:00\tTime", "PSTAT\tPSTAT\tREF600-12345\tPotentiostat"]
    lines += [f"{k}\tQUANT\t{v}\t{label}" for k, (v, label) in (quants or {}).items()]
    for name, cols, units, rows in tables:
        lines += [f"{name}\tTABLE\t{len(rows)}\tCurve", "\t" + "\t".join(cols), "\t" + "\t".join(units)]
        lines += ["\t" + "\t".join(num(v) for v in r) for r in rows]
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("latin-1"))
