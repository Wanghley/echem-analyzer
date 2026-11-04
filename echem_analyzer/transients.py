"""Transients: response time, steady state and drift, Cottrell and Anson analysis (no Qt)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .calibration import fit_linear

NAN = float("nan")
F_FARADAY = 96485.33212


@dataclass
class SteadyState:
    level: float = NAN
    sd: float = NAN
    drift: float = NAN                  # units per second (linear fit slope over the window)
    drift_rel_pct_per_min: float = NAN
    snr: float = NAN
    n: int = 0


def steady_state(t, y, t0: float, t1: float) -> SteadyState:
    """Mean, noise, drift and signal-to-noise of a stretch of a trace."""
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    m = (t >= t0) & (t <= t1) & np.isfinite(y)
    out = SteadyState(n=int(m.sum()))
    if m.sum() < 3:
        return out
    tt, yy = t[m], y[m]
    f = fit_linear(tt, yy)
    out.level, out.sd = float(yy.mean()), float(yy.std(ddof=1))
    if f.ok:
        out.drift = f.p("slope")
        resid = yy - f.predict(tt)
        out.sd = float(resid.std(ddof=2)) if len(yy) > 2 else out.sd           # noise about the trend, not drift
        if out.level:
            out.drift_rel_pct_per_min = out.drift * 60.0 / abs(out.level) * 100.0
    out.snr = abs(out.level) / out.sd if out.sd else NAN
    return out


@dataclass
class ResponseTime:
    y_start: float = NAN
    y_end: float = NAN
    t_step: float = NAN
    t_low: float = NAN                  # time after the step to reach `low` of the change
    t_high: float = NAN
    rise: float = NAN                   # t_high - t_low (e.g. 10-90 % rise time)
    low: float = 0.1
    high: float = 0.9
    warnings: list[str] = field(default_factory=list)


def response_time(t, y, t_step: float, pre: float = 5.0, post_window: float = 10.0, t_end: Optional[float] = None,
                  low: float = 0.1, high: float = 0.9) -> ResponseTime:
    """Time for a response to a step to reach ``low`` and ``high`` of its final change (default 10 % and 90 %).

    The start level is the mean over ``pre`` seconds before ``t_step``; the end level is the mean over the last
    ``post_window`` seconds before ``t_end`` (default: the end of the trace).  Times are measured from ``t_step``.
    """
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    out = ResponseTime(t_step=t_step, low=low, high=high)
    t_end = float(t[-1]) if t_end is None else t_end
    pre_m = (t >= t_step - pre) & (t < t_step)
    post_m = (t >= t_end - post_window) & (t <= t_end)
    if pre_m.sum() < 2 or post_m.sum() < 2:
        out.warnings.append("Not enough points before the step or at the end to define the two levels.")
        return out
    y0, y1 = float(y[pre_m].mean()), float(y[post_m].mean())
    out.y_start, out.y_end = y0, y1
    if y1 == y0:
        out.warnings.append("No change between the two levels.")
        return out
    after = (t >= t_step) & (t <= t_end)
    ta, ya = t[after], y[after]
    frac = (ya - y0) / (y1 - y0)

    def first_cross(level: float) -> float:
        idx = np.flatnonzero(frac >= level)
        if idx.size == 0:
            return NAN
        j = int(idx[0])
        if j == 0:
            return float(ta[0] - t_step)
        return float(ta[j - 1] + (level - frac[j - 1]) * (ta[j] - ta[j - 1]) / (frac[j] - frac[j - 1]) - t_step)

    out.t_low, out.t_high = first_cross(low), first_cross(high)
    out.rise = out.t_high - out.t_low if math.isfinite(out.t_low) and math.isfinite(out.t_high) else NAN
    if not math.isfinite(out.t_high):
        out.warnings.append("The response never reached the high level.")
    return out


@dataclass
class CottrellResult:
    slope: float = NAN                  # i vs t^-1/2 (µA·s^½)
    intercept: float = NAN
    r2: float = NAN
    d_cm2s: float = NAN
    n: int = 0
    warnings: list[str] = field(default_factory=list)


def cottrell(t, i_ua, t0: float, t1: float, n_e: int = 1, area_cm2: float = 0.0, conc_mM: float = 0.0) -> CottrellResult:
    """Cottrell analysis: i = nFAC√D / √(π t).  Fit |i| against t^-1/2 in [t0, t1]; D if A and C are given."""
    t = np.asarray(t, float)
    i = np.asarray(i_ua, float)
    m = (t > 0) & (t >= t0) & (t <= t1) & np.isfinite(i)
    out = CottrellResult(n=int(m.sum()))
    if m.sum() < 4:
        out.warnings.append("Too few points in the window.")
        return out
    f = fit_linear(t[m] ** -0.5, np.abs(i[m]))
    if not f.ok:
        out.warnings.append(f.message)
        return out
    out.slope, out.intercept, out.r2 = f.p("slope"), f.p("intercept"), f.r2
    if area_cm2 > 0 and conc_mM > 0:
        k = n_e * F_FARADAY * area_cm2 * conc_mM * 1e-6 / math.sqrt(math.pi) * 1e6      # µA·s^½ per √D
        out.d_cm2s = (abs(out.slope) / k) ** 2
    if out.r2 < 0.98:
        out.warnings.append("|i| is not linear in t⁻¹ᐟ² (R² < 0.98): not a clean diffusion-limited transient in this window.")
    return out


@dataclass
class AnsonResult:
    slope: float = NAN                  # Q vs t^1/2 (µC·s^-½)
    intercept: float = NAN              # µC: double-layer charge + adsorbed charge
    r2: float = NAN
    d_cm2s: float = NAN
    gamma_mol_cm2: float = NAN
    n: int = 0
    warnings: list[str] = field(default_factory=list)


def anson(t, q_uc, t0: float, t1: float, n_e: int = 1, area_cm2: float = 0.0, conc_mM: float = 0.0,
          q_dl_uc: float = 0.0) -> AnsonResult:
    """Anson plot: Q = 2nFAC√(D t/π) + Q_dl + nFAΓ.  Slope gives D; intercept (minus Q_dl) gives the adsorbed amount."""
    t = np.asarray(t, float)
    q = np.asarray(q_uc, float)
    m = (t > 0) & (t >= t0) & (t <= t1) & np.isfinite(q)
    out = AnsonResult(n=int(m.sum()))
    if m.sum() < 4:
        out.warnings.append("Too few points in the window.")
        return out
    f = fit_linear(np.sqrt(t[m]), q[m])
    if not f.ok:
        out.warnings.append(f.message)
        return out
    out.slope, out.intercept, out.r2 = f.p("slope"), f.p("intercept"), f.r2
    if area_cm2 > 0 and conc_mM > 0:
        k = 2 * n_e * F_FARADAY * area_cm2 * conc_mM * 1e-6 / math.sqrt(math.pi) * 1e6    # µC·s^-½ per √D
        out.d_cm2s = (abs(out.slope) / k) ** 2
    if area_cm2 > 0:
        out.gamma_mol_cm2 = (out.intercept - q_dl_uc) * 1e-6 / (n_e * F_FARADAY * area_cm2)
    return out
