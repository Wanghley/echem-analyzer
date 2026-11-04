"""Sensor-study statistics: reproducibility, stability, selectivity and recovery (no Qt)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np
from scipy import optimize, stats

from .calibration import fit_linear

NAN = float("nan")


@dataclass
class GroupStats:
    n: int = 0
    mean: float = NAN
    sd: float = NAN
    rsd_pct: float = NAN
    sem: float = NAN
    ci95: float = NAN                    # half-width of the 95 % confidence interval of the mean (Student t)
    minimum: float = NAN
    maximum: float = NAN


def group_stats(values: Sequence[float]) -> GroupStats:
    v = np.asarray([x for x in values if x is not None and np.isfinite(x)], float)
    g = GroupStats(n=int(v.size))
    if v.size == 0:
        return g
    g.mean, g.minimum, g.maximum = float(v.mean()), float(v.min()), float(v.max())
    if v.size > 1:
        g.sd = float(v.std(ddof=1))
        g.sem = g.sd / math.sqrt(v.size)
        g.ci95 = float(stats.t.ppf(0.975, v.size - 1) * g.sem)
        g.rsd_pct = abs(g.sd / g.mean) * 100.0 if g.mean else NAN
    return g


def grubbs_outliers(values: Sequence[float], alpha: float = 0.05) -> list[int]:
    """Indices flagged as outliers by repeated two-sided Grubbs tests (needs n >= 3)."""
    v = np.asarray(values, float)
    idx = list(range(len(v)))
    flagged: list[int] = []
    while len(idx) >= 3:
        cur = v[idx]
        sd = cur.std(ddof=1)
        if sd == 0:
            break
        z = np.abs(cur - cur.mean()) / sd
        k = int(np.argmax(z))
        n = len(cur)
        t2 = stats.t.ppf(1 - alpha / (2 * n), n - 2) ** 2
        g_crit = (n - 1) / math.sqrt(n) * math.sqrt(t2 / (n - 2 + t2))
        if z[k] > g_crit:
            flagged.append(idx.pop(k))
        else:
            break
    return sorted(flagged)


@dataclass
class StabilityResult:
    t: np.ndarray
    y: np.ndarray
    retention_pct: np.ndarray
    drift_per_unit: float = NAN          # linear slope of y vs t
    drift_pct_per_unit: float = NAN      # slope relative to the first value
    tau: float = NAN                     # exponential time constant (same unit as t) if the response decays
    y_inf: float = NAN                   # the exponential fit's asymptote
    half_life: float = NAN
    t_90: float = NAN                    # time at which retention falls to 90 % (linear trend)
    final_retention_pct: float = NAN
    warnings: list[str] = field(default_factory=list)


def stability(t, y, reference: str = "first") -> StabilityResult:
    """Retention, drift and (if it decays) exponential half-life of a response measured over time.

    ``reference``: ``"first"`` (the first measurement) or ``"max"`` (the highest response).
    """
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    ok = np.isfinite(t) & np.isfinite(y)
    t, y = t[ok], y[ok]
    order = np.argsort(t)
    t, y = t[order], y[order]
    res = StabilityResult(t, y, np.full_like(y, np.nan))
    if len(t) < 2:
        res.warnings.append("A stability study needs at least two time points.")
        return res
    ref = y[0] if reference == "first" else (y[np.argmax(np.abs(y))])
    if ref == 0:
        res.warnings.append("The reference response is zero.")
        return res
    res.retention_pct = y / ref * 100.0
    res.final_retention_pct = float(res.retention_pct[-1])
    f = fit_linear(t, y)
    if f.ok:
        res.drift_per_unit = f.p("slope")
        res.drift_pct_per_unit = f.p("slope") / abs(ref) * 100.0
        if res.drift_per_unit != 0 and f.p("slope") * ref < 0:                 # decreasing in magnitude
            t90 = (0.9 * ref - f.p("intercept")) / f.p("slope")
            res.t_90 = float(t90) if t90 > t[0] else NAN
    if len(t) >= 4 and res.retention_pct[-1] < 100:
        try:
            func = lambda tt, yinf, tau: yinf + (y[0] - yinf) * np.exp(-(tt - t[0]) / tau)       # noqa: E731
            popt, _ = optimize.curve_fit(func, t, y, p0=[y[-1], max(np.ptp(t), 1e-6)], bounds=([-np.inf, 1e-9], [np.inf, np.inf]))
            if abs(popt[0]) < abs(y[0]):
                res.tau = float(popt[1])
                res.y_inf = float(popt[0])
                res.half_life = res.tau * math.log(2)
        except (RuntimeError, ValueError):
            pass
    return res


@dataclass
class SelectivityRow:
    interferent: str
    response: float
    conc: float
    interference_pct: float = NAN        # response ratio at equal concentration x 100
    k_sel: float = NAN                   # (y_int / c_int) / (y_analyte / c_analyte): sensitivity ratio
    change_pct: float = NAN              # for mixtures: change relative to the analyte alone


def selectivity(analyte_response: float, analyte_conc: float, interferents: Sequence[tuple[str, float, float]]) -> list[SelectivityRow]:
    """Interference table: ``interferents`` is a list of (name, response, concentration).

    interference % = 100·y_int/y_analyte when both were measured at the same concentration; otherwise the
    selectivity coefficient K = (y_int/c_int)/(y_a/c_a) (ratio of sensitivities) is the comparable number.
    """
    rows = []
    for name, y, c in interferents:
        r = SelectivityRow(name, y, c)
        if analyte_response:
            r.change_pct = (y - analyte_response) / abs(analyte_response) * 100.0
            if c and analyte_conc:
                r.k_sel = (y / c) / (analyte_response / analyte_conc)
            if c == analyte_conc:
                r.interference_pct = y / analyte_response * 100.0
        rows.append(r)
    return rows


@dataclass
class RecoveryRow:
    label: str
    expected: float
    found: float
    recovery_pct: float = NAN
    found_se: float = NAN


@dataclass
class RecoverySummary:
    rows: list[RecoveryRow]
    mean_pct: float = NAN
    sd_pct: float = NAN
    rsd_pct: float = NAN


def recovery(rows: Sequence[tuple[str, float, float]]) -> RecoverySummary:
    """Recovery % = 100·found/expected for spiked samples; ``rows`` = (label, expected, found)."""
    out = [RecoveryRow(l, e, f, f / e * 100.0 if e else NAN) for l, e, f in rows]
    g = group_stats([r.recovery_pct for r in out])
    return RecoverySummary(out, g.mean, g.sd, g.rsd_pct)
