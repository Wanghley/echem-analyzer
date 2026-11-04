"""Electrode-characterisation studies built from ticked CV runs (no Qt).

The Electrode tab and the reports both call these functions, so the numbers in a report are exactly the ones on screen.
What the user ticked, which cycle of each run, and the probe / electrode parameters live in ``extras["electrode"]``:
``{"params": {...}, "scan_rate": {"use": {uid: bool}, "cycle": {uid: n}}, "cdl": {...}}``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import Callable, Optional, Sequence

import numpy as np

from . import electrode
from .cv_analysis import CVRun, analyze_cycle, sweep_masks
from .models import CVSettings

Selection = list[tuple[CVRun, CVSettings, int]]


@dataclass
class ElectrodeParams:
    n: int = 1
    conc: float = 5.0              # mM redox probe
    D: float = 7.6e-6              # cm²/s ([Fe(CN)6]3- in 0.1 M KCl)
    area: float = 0.0              # cm², geometric
    temp: float = 25.0             # °C
    e_mid: float = 0.0             # V, in the non-faradaic window (Cdl)
    cdl_area: float = 0.0          # cm²
    cs: float = 0.0                # µF/cm², specific capacitance of a smooth surface

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "ElectrodeParams":
        names = {f.name for f in fields(cls)}
        p = cls()
        for k, v in (d or {}).items():
            if k in names:
                try:
                    setattr(p, k, int(v) if k == "n" else float(v))
                except (TypeError, ValueError):
                    pass
        return p


def params_from(extras: dict) -> ElectrodeParams:
    return ElectrodeParams.from_dict((extras.get("electrode") or {}).get("params"))


def state_from(extras: dict, key: str) -> dict:
    """The stored selection for ``key`` ("scan_rate" or "cdl"), without creating it."""
    st = (extras.get("electrode") or {}).get(key) or {}
    return {"use": dict(st.get("use") or {}), "cycle": dict(st.get("cycle") or {})}


def select_runs(runs: Sequence[CVRun], settings_of: Callable[[CVRun], CVSettings], state: dict) -> Selection:
    """(run, its settings, the cycle number to use) for every ticked run; the last used cycle unless one was chosen."""
    out: Selection = []
    for run in runs:
        if not state["use"].get(run.uid, False):
            continue
        s = settings_of(run)
        n = state["cycle"].get(run.uid, 0)
        if n not in run.cycles:
            used = [c for c in run.cycle_numbers if c not in s.excluded_cycles] or run.cycle_numbers
            n = used[-1]
        out.append((run, s, n))
    return out


def scan_rate_rows(sel: Selection) -> tuple[list[tuple[float, float, float, float, float]], dict[str, list[float]]]:
    """Rows (ν, ipa, ipc, Epa, Epc) of the usable runs, and the peak values of every run for the table."""
    rows, per_run = [], {}
    for run, s, n in sel:
        c = run.cycles[n]
        r = analyze_cycle(c.x, c.y, s, n)
        per_run[run.uid] = [r.i_pa, r.i_pc, r.e_pa, r.e_pc]
        if s.scan_rate > 0 and all(math.isfinite(v) for v in (r.i_pa, r.i_pc, r.e_pa, r.e_pc)):
            rows.append((s.scan_rate, r.i_pa, r.i_pc, r.e_pa, r.e_pc))
    return rows, per_run


def run_scan_rate(sel: Selection, p: ElectrodeParams):
    """(analysis or None, per-run peak values). ``None`` while fewer than three runs have a scan rate and clear peaks."""
    rows, per_run = scan_rate_rows(sel)
    if len(rows) < 3:
        return None, per_run
    nu, ia, ic, ea, ec = (np.array(v, float) for v in zip(*rows))
    return electrode.scan_rate_analysis(nu, ia, ic, ea, ec, p.n, p.conc, p.D, p.area, p.temp), per_run


def cdl_rows(sel: Selection, e_mid: float) -> tuple[list[tuple[float, float]], dict[str, float]]:
    """Rows (ν, (i_up − i_down)/2 at E_mid) and the half gap of every run."""
    rows, per_run = [], {}
    for run, s, n in sel:
        c = run.cycles[n]
        up, down = sweep_masks(c.x)
        if up.sum() < 2 or down.sum() < 2:
            continue
        i_up = float(np.interp(e_mid, c.x[up], c.y[up]))
        i_dn = float(np.interp(e_mid, c.x[down][::-1], c.y[down][::-1]))
        half = (i_up - i_dn) / 2.0
        per_run[run.uid] = half
        if s.scan_rate > 0:
            rows.append((s.scan_rate, half))
    return rows, per_run


def run_cdl(sel: Selection, p: ElectrodeParams):
    """(ν array, half-gap array, result dict, per-run half gaps); the dict is empty with fewer than three usable runs."""
    rows, per_run = cdl_rows(sel, p.e_mid)
    if len(rows) < 3:
        return np.array([]), np.array([]), {}, per_run
    nu, di = (np.array(v, float) for v in zip(*rows))
    return nu, di, electrode.double_layer_capacitance(nu, di, p.cdl_area, p.cs), per_run
