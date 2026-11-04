"""Cyclic-voltammetry analysis for multi-cycle runs (pure numpy, no GUI imports).

A *run* is the set of cycles (PalmSens "scans") measured together.  For every cycle we
find the anodic and cathodic peak on the matching sweep branch, integrate the charge, and
estimate an apparent capacitance; across cycles we report how much the peaks still change,
which tells you whether the electrode is conditioned.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import numpy as np

from .models import KIND_CV, CVCycleResult, CVSettings, Curve

NAN = float("nan")


# --------------------------------------------------------------------------- grouping

@dataclass
class CVRun:
    """The cycles of one CV measurement, keyed by cycle (scan) number."""

    uid: str
    title: str                       # measurement title, e.g. "Cyclic Voltammetry" or "A6 CV"
    source: str                      # file the cycles came from
    cycles: dict[int, Curve]
    meta: dict

    @property
    def cycle_numbers(self) -> list[int]:
        return sorted(self.cycles)

    @property
    def scan_rate(self) -> float:
        return float(self.meta.get("scan_rate", 0.0) or 0.0)


def group_cv_runs(curves: Iterable[Curve]) -> list[CVRun]:
    """Group CV curves into runs (same file and measurement), preserving file order."""
    runs: dict[str, CVRun] = {}
    for c in curves:
        if c.kind != KIND_CV or c.scan is None:
            continue
        title = c.meta.get("run_title", "") or "Cyclic Voltammetry"
        mi = c.meta.get("measurement_index")
        uid = f"{c.source}|cv|{'' if mi is None else mi}|{title}"
        run = runs.get(uid)
        if run is None:
            run = runs[uid] = CVRun(uid=uid, title=title, source=c.source, cycles={}, meta={})
        run.cycles[int(c.scan)] = c
        for k in ("scan_rate", "e_begin", "e_vtx1", "e_vtx2", "e_step"):
            if k in c.meta:
                run.meta.setdefault(k, c.meta[k])
    return list(runs.values())


# --------------------------------------------------------------------------- one cycle

def sweep_masks(e: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Boolean masks of the samples on a positive-going (anodic) / negative-going sweep.

    Classification is by the sign of the next potential step, so it works for any waveform
    (several vertices, any start potential).  The last sample takes the previous direction.
    """
    e = np.asarray(e, float)
    if len(e) < 2:
        return np.zeros(len(e), bool), np.zeros(len(e), bool)
    d = np.diff(e)
    d = np.r_[d, d[-1]]
    return d > 0, d < 0


def _in_window(e: np.ndarray, window: Optional[Sequence[float]]) -> np.ndarray:
    if not window:
        return np.ones(len(e), bool)
    lo, hi = sorted(float(w) for w in window)
    return (e >= lo) & (e <= hi)


def _peak(e: np.ndarray, i: np.ndarray, mask: np.ndarray, baseline: str, sign: float):
    """(E, height, raw current) of the extreme of ``sign * (i - baseline)`` over ``mask``.

    ``sign`` is +1 for the anodic maximum and -1 for the cathodic minimum.  With a linear
    baseline, a line is drawn through the first and last sample of the search window and
    the peak height is measured from it.  All NaN if the window holds no samples.
    """
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return NAN, NAN, NAN
    ee, ii = e[idx], i[idx]
    if baseline == "linear" and idx.size >= 2 and ee[-1] != ee[0]:
        base = ii[0] + (ii[-1] - ii[0]) * (ee - ee[0]) / (ee[-1] - ee[0])
        h = ii - base
    else:
        h = ii
    k = int(np.argmax(sign * h))
    return float(ee[k]), float(h[k]), float(ii[k])


def window_baselines(e: np.ndarray, i: np.ndarray, s: CVSettings) -> dict[str, Optional[tuple]]:
    """End points ``((E0, i0), (E1, i1))`` of the linear baseline of each peak window (for drawing).

    Empty unless ``s.baseline == "linear"``; a value is ``None`` when its window is unrestricted
    (no baseline is used then) or holds fewer than two samples.
    """
    out: dict[str, Optional[tuple]] = {}
    if s.baseline != "linear":
        return out
    e = np.asarray(e, float)
    i = np.asarray(i, float)
    up, down = sweep_masks(e)
    for name, mask, win in (("anodic", up, s.anodic_window), ("cathodic", down, s.cathodic_window)):
        idx = np.flatnonzero(mask & _in_window(e, win)) if win else np.array([], dtype=int)
        out[name] = ((float(e[idx[0]]), float(i[idx[0]])), (float(e[idx[-1]]), float(i[idx[-1]]))) \
            if idx.size >= 2 else None
    return out


def analyze_cycle(
    e: np.ndarray, i: np.ndarray, s: CVSettings, cycle: int = 0
) -> CVCycleResult:
    """Peaks, charge and capacitance of a single cycle."""
    e = np.asarray(e, float)
    i = np.asarray(i, float)
    res = CVCycleResult(cycle=cycle, n_points=len(e))
    if len(e) < 5:
        return res

    up, down = sweep_masks(e)
    # A linear baseline needs a window around the peak: across a whole unrestricted sweep it would be
    # a meaningless diagonal, so an unrestricted search always uses the raw current.
    base_a = s.baseline if s.anodic_window else "none"
    base_c = s.baseline if s.cathodic_window else "none"
    res.e_pa, res.i_pa, res.i_pa_raw = _peak(e, i, up & _in_window(e, s.anodic_window), base_a, +1.0)
    res.e_pc, res.i_pc, res.i_pc_raw = _peak(e, i, down & _in_window(e, s.cathodic_window), base_c, -1.0)
    if math.isfinite(res.e_pa) and math.isfinite(res.e_pc):
        res.e_half = 0.5 * (res.e_pa + res.e_pc)
        res.delta_ep = res.e_pa - res.e_pc
    if math.isfinite(res.i_pa) and math.isfinite(res.i_pc) and res.i_pc != 0:
        res.ip_ratio = abs(res.i_pa / res.i_pc)

    if s.scan_rate > 0:
        de = np.abs(np.diff(e))
        dt = de / s.scan_rate                                  # s per step
        seg = 0.5 * (i[:-1] + i[1:]) * dt                      # µA·s = µC per step
        res.q_anodic = float(seg[up[:-1]].sum())
        res.q_cathodic = float(seg[down[:-1]].sum())
        span = float(e.max() - e.min())
        if span > 0:
            res.capacitance = (res.q_anodic - res.q_cathodic) / (2.0 * span)
    return res


def analyze_run(run: CVRun, s: CVSettings, include_excluded: bool = False) -> list[CVCycleResult]:
    """Analyse every (non-excluded) cycle of ``run``, ordered by cycle number."""
    out = []
    for n in run.cycle_numbers:
        if not include_excluded and n in s.excluded_cycles:
            continue
        c = run.cycles[n]
        out.append(analyze_cycle(c.x, c.y, s, cycle=n))
    return out


# --------------------------------------------------------------------------- stability

@dataclass
class Stability:
    cycles: list[int]
    values: list[float]
    rel_change_pct: list[float]        # change relative to the previous cycle; first entry NaN
    stable_from: Optional[int]         # first cycle after which every change is below the tolerance
    total_drift_pct: float             # last vs first cycle
    last_change_pct: float             # last vs previous cycle


def stability(cycles: Sequence[int], values: Sequence[float], tol_pct: float) -> Stability:
    """How much does a per-cycle quantity (e.g. the anodic peak current) still change?"""
    cyc = list(cycles)
    val = [float(v) for v in values]
    rel = [NAN] * len(val)
    for k in range(1, len(val)):
        a, b = val[k - 1], val[k]
        if math.isfinite(a) and math.isfinite(b) and a != 0:
            rel[k] = abs(b - a) / abs(a) * 100.0

    stable_from = None
    if len(val) >= 2 and all(math.isfinite(r) for r in rel[1:]):
        # walk back from the end while the change into each cycle is below the tolerance;
        # the signal has settled at the cycle just before the first such change
        j = len(val)
        while j - 1 >= 1 and rel[j - 1] < tol_pct:
            j -= 1
        if j < len(val):                                    # at least the last change is below tol
            stable_from = cyc[j - 1]
    drift = NAN
    if len(val) >= 2 and math.isfinite(val[0]) and math.isfinite(val[-1]) and val[0] != 0:
        drift = (val[-1] - val[0]) / abs(val[0]) * 100.0
    return Stability(
        cycles=cyc, values=val, rel_change_pct=rel, stable_from=stable_from,
        total_drift_pct=drift, last_change_pct=rel[-1] if rel else NAN,
    )


def summarize_stability(results: Sequence[CVCycleResult], tol_pct: float) -> dict[str, Stability]:
    """Stability of the anodic and cathodic peak currents across the analysed cycles."""
    cyc = [r.cycle for r in results]
    return {
        "i_pa": stability(cyc, [r.i_pa for r in results], tol_pct),
        "i_pc": stability(cyc, [r.i_pc for r in results], tol_pct),
    }
