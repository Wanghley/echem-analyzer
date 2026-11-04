"""Chronoamperometry standard-addition analysis (pure numpy, no GUI imports).

Pipeline
--------
detect_steps  ->  analyze_ca:
    segment the trace at the addition times, extract one plateau current per segment,
    convert additions to concentrations, fit signal vs. added concentration,
    and derive sensitivity, LOD/LOQ and the standard-addition x-intercept.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import find_peaks

from .models import CAAnalysisSettings, CAResult, FitResult

NAN = float("nan")


# --------------------------------------------------------------------------- step detection

def _robust_sigma(a: np.ndarray) -> float:
    a = a[np.isfinite(a)]
    if a.size == 0:
        return 0.0
    return float(1.4826 * np.median(np.abs(a - np.median(a))))


def detect_step_events(
    t: np.ndarray,
    i: np.ndarray,
    lag: float = 0.5,
    k: float = 8.0,
    min_sep: float = 5.0,
    expected: int = 0,
    abruptness: float = 0.25,
) -> tuple[np.ndarray, np.ndarray]:
    """Find abrupt current jumps; return ``(steps, ignored)`` as arrays of times (s).

    A candidate is a peak of the lagged difference ``d(t) = i(t+lag) - i(t-lag)`` of the
    median-filtered trace that exceeds ``k`` robust standard deviations (and a small
    fraction of the largest jump).  It must also be *abrupt*: at least ``abruptness`` of
    its total change has to happen between two consecutive samples.  That rejects smooth
    decays (e.g. the start-up transient of a conditioning run) while keeping real
    additions, whose change is concentrated in one or two samples.

    ``steps`` are the jumps in the dominant direction (the additions).  ``ignored`` are
    significant jumps in the *opposite* direction (a bubble, a stirring change, ...): they
    are not additions, but the caller should tell the user about them because they
    corrupt the plateau of the segment they fall in.
    """
    t = np.asarray(t, float)
    i = np.asarray(i, float)
    n = len(t)
    empty = (np.array([]), np.array([]))
    if n < 10:
        return empty
    dt = float(np.median(np.diff(t)))
    L = max(1, int(round(lag / dt)))
    if n <= 2 * L + 2:
        return empty

    # 'mirror' padding: a single-sample spike at either end is outvoted by the median
    # instead of being replicated into its own window ('nearest' would keep it).
    f = median_filter(i, size=5, mode="mirror")
    d = np.zeros(n)
    d[L : n - L] = f[2 * L :] - f[: n - 2 * L]
    ad = np.abs(d)

    sigma = _robust_sigma(d[L : n - L])
    thr = max(k * sigma, 0.03 * ad.max())
    if thr <= 0:
        return empty

    distance = max(1, int(round(min_sep / dt)))
    peaks, _ = find_peaks(ad, height=thr, distance=distance)

    g = np.abs(np.diff(f))           # jump between sample j and j+1
    tm = 0.5 * (t[:-1] + t[1:])

    # abruptness: the largest single-sample jump inside the lag window must carry a
    # sizeable share of the peak's total change
    peaks = np.array(
        [p for p in peaks if g[max(0, p - L) : min(len(g), p + L)].max() >= abruptness * ad[p]],
        dtype=int,
    )
    if peaks.size == 0:
        return empty

    signs = np.sign(d[peaks])
    dominant = 1.0 if np.sum(d[peaks]) >= 0 else -1.0
    step_peaks = peaks[signs == dominant]
    other_peaks = peaks[signs != dominant]

    if expected and step_peaks.size > expected:
        keep = np.argsort(ad[step_peaks])[::-1][:expected]
        step_peaks = np.sort(step_peaks[keep])

    def refine(p: int) -> float:
        """Centroid of the single-sample jumps around peak ``p`` (sub-sample step time)."""
        lo, hi = max(0, p - L), min(len(g), p + L)
        w = g[lo:hi]
        if w.size == 0 or w.max() <= 0:
            return float(t[p])
        w = np.where(w >= 0.25 * w.max(), w, 0.0)
        return float(np.sum(tm[lo:hi] * w) / np.sum(w))

    return (
        np.array(sorted(refine(p) for p in step_peaks)),
        np.array(sorted(refine(p) for p in other_peaks)),
    )


def detect_steps(
    t: np.ndarray,
    i: np.ndarray,
    lag: float = 0.5,
    k: float = 8.0,
    min_sep: float = 5.0,
    expected: int = 0,
) -> np.ndarray:
    """Times (s) of the abrupt current steps produced by analyte additions."""
    return detect_step_events(t, i, lag, k, min_sep, expected)[0]


# --------------------------------------------------------------------------- helpers

def sync_step_lists(s: CAAnalysisSettings) -> None:
    """Make the per-step lists the same length as ``s.step_times`` (pad / truncate)."""
    n = len(s.step_times)

    def fit_list(lst: list, fill):
        if len(lst) > n:
            del lst[n:]
        while len(lst) < n:
            lst.append(fill)

    fit_list(s.step_volumes_ul, s.v_add_ul)
    fit_list(s.step_conc_override, None)
    fit_list(s.step_included, True)


def sort_steps(s: CAAnalysisSettings) -> None:
    """Sort the steps by time, keeping the per-step lists aligned."""
    sync_step_lists(s)
    if s.step_times == sorted(s.step_times):
        return
    order = sorted(range(len(s.step_times)), key=lambda j: s.step_times[j])
    s.step_times = [s.step_times[j] for j in order]
    s.step_volumes_ul = [s.step_volumes_ul[j] for j in order]
    s.step_conc_override = [s.step_conc_override[j] for j in order]
    s.step_included = [s.step_included[j] for j in order]


def _window_sel(t: np.ndarray, a: float, b: float) -> np.ndarray:
    return (t >= a) & (t <= b)


def _mean_sd(t: np.ndarray, i: np.ndarray, a: float, b: float) -> tuple[float, float, int]:
    m = _window_sel(t, a, b)
    n = int(m.sum())
    if n == 0:
        return NAN, NAN, 0
    seg = i[m]
    return float(seg.mean()), float(seg.std(ddof=1)) if n > 1 else 0.0, n


def _line_at(t: np.ndarray, i: np.ndarray, a: float, b: float, at: float) -> float:
    """Value at time ``at`` of a straight line fitted to samples in [a, b]."""
    m = _window_sel(t, a, b)
    n = int(m.sum())
    if n == 0:
        return NAN
    if n < 3:
        return float(i[m].mean())
    slope, icpt = np.polyfit(t[m], i[m], 1)
    return float(slope * at + icpt)


def linear_fit(x: Sequence[float], y: Sequence[float]) -> Optional[FitResult]:
    """Ordinary least squares with the usual uncertainty estimates."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    n = len(x)
    if n < 2:
        return None
    xm, ym = x.mean(), y.mean()
    sxx = float(np.sum((x - xm) ** 2))
    if sxx <= 0:
        return None
    slope = float(np.sum((x - xm) * (y - ym)) / sxx)
    icpt = float(ym - slope * xm)
    res = y - (slope * x + icpt)
    ss_res = float(np.sum(res**2))
    ss_tot = float(np.sum((y - ym) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else NAN
    if n > 2:
        s_yx = math.sqrt(ss_res / (n - 2))
        slope_se = s_yx / math.sqrt(sxx)
        icpt_se = s_yx * math.sqrt(1.0 / n + xm**2 / sxx)
    else:
        s_yx = slope_se = icpt_se = NAN
    if slope != 0:
        x_int = -icpt / slope
        # Harris / Miller & Miller: uncertainty of an x value read from the fit at y = 0
        x_int_se = (
            (s_yx / abs(slope)) * math.sqrt(1.0 / n + ym**2 / (slope**2 * sxx))
            if n > 2
            else NAN
        )
    else:
        x_int = x_int_se = NAN
    return FitResult(
        slope=slope, intercept=icpt, slope_se=slope_se, intercept_se=icpt_se, r2=r2,
        s_yx=s_yx, n=n, x=x, y=y, residuals=res, x_intercept=x_int, x_intercept_se=x_int_se,
    )


def added_concentrations(
    stock: float,
    v0_ml: float,
    volumes_ul: Sequence[float],
    dilution: bool = True,
    overrides: Optional[Sequence[Optional[float]]] = None,
) -> np.ndarray:
    """Added analyte concentration after each addition (index 0 = before any addition).

    ``C_k = C_s * sum(V) / (V0 + sum(V))`` with dilution, ``C_s * sum(V) / V0`` without.
    Volumes are in µL, V0 in mL.  Returns concentrations in the stock's unit.
    """
    n = len(volumes_ul)
    out = np.zeros(n + 1)
    v0_ul = v0_ml * 1000.0
    cum = 0.0
    for k in range(n):
        cum += float(volumes_ul[k] or 0.0)
        if v0_ul <= 0 or stock <= 0:
            out[k + 1] = NAN
        else:
            denom = v0_ul + cum if dilution else v0_ul
            out[k + 1] = stock * cum / denom
        if overrides is not None and k < len(overrides) and overrides[k] is not None:
            out[k + 1] = float(overrides[k])
    return out


def baseline_sigma(
    t: np.ndarray, i: np.ndarray, t_first_step: float, window: float, guard: float
) -> float:
    """Std. dev. of the linearly detrended baseline just before the first addition."""
    b = t_first_step - guard
    m = _window_sel(t, b - window, b)
    if m.sum() < 5:
        return NAN
    tt, yy = t[m], i[m]
    p = np.polyfit(tt, yy, 1)
    return float(np.std(yy - np.polyval(p, tt), ddof=2))


# --------------------------------------------------------------------------- main pipeline

def analyze_ca(t: np.ndarray, i: np.ndarray, s: CAAnalysisSettings) -> CAResult:
    t = np.asarray(t, float)
    i = np.asarray(i, float)
    if s.drop_last > 0:
        t, i = t[: -s.drop_last], i[: -s.drop_last]
    sync_step_lists(s)

    warnings: list[str] = []
    sort_steps(s)
    steps = np.array(s.step_times, float)

    if s.ignored_events:
        when = ", ".join(f"{x:.1f} s" for x in s.ignored_events)
        warnings.append(
            f"Jump(s) in the opposite direction at {when} were not treated as additions "
            "(bubble, stirring change, disconnection?). They corrupt the plateau of the segment "
            "they fall in: inspect it and untick ('Use') the affected points."
        )

    n_seg = len(steps) + 1
    edges = [float(t[0])] + [float(x) for x in steps] + [float(t[-1])]
    seg_bounds = [(edges[k], edges[k + 1]) for k in range(n_seg)]

    guard = max(0.0, s.end_guard)
    plateau = np.full(n_seg, NAN)
    plateau_sd = np.full(n_seg, NAN)
    win_bounds: list[tuple[float, float]] = []

    # --- averaging windows (end-of-segment for 'plateau', just-after-step for 'drift')
    for k, (a, b) in enumerate(seg_bounds):
        last = k == n_seg - 1
        b_eff = b if last else b - guard
        if s.method == "drift" and k > 0:
            w0 = a + s.settle
            w1 = min(w0 + s.window, b_eff)
        else:
            w1 = b_eff
            w0 = max(a + s.settle, w1 - s.window)
        if w1 - w0 < 0.5:      # segment too short for the requested settle time
            w0 = a + 0.5 * (b_eff - a)
            w1 = b_eff
            warnings.append(f"Segment {k}: too short for settle={s.settle:g}s; using its second half.")
        win_bounds.append((w0, w1))
        plateau[k], plateau_sd[k], _ = _mean_sd(t, i, w0, w1)

    # --- response relative to the baseline segment
    delta = np.zeros(n_seg)
    if s.method == "drift" and len(steps) > 0:
        h = np.zeros(n_seg)
        for k in range(1, n_seg):
            ts = steps[k - 1]
            pa, pb = seg_bounds[k - 1]
            pre1 = ts - guard
            pre0 = max(pa + s.settle, pre1 - s.window)
            post0, post1 = win_bounds[k]
            h[k] = _line_at(t, i, post0, post1, ts) - _line_at(t, i, pre0, pre1, ts)
        delta = np.cumsum(h)
    else:
        delta = plateau - plateau[0]

    base0 = plateau[0]
    levels = base0 + delta
    finite = levels[np.isfinite(levels)]
    polarity = -1.0 if finite.size and np.median(finite) < 0 else 1.0

    # --- concentrations and signal
    v_ul = list(s.step_volumes_ul)
    conc = added_concentrations(
        s.stock_conc, s.v0_ml, v_ul, s.dilution_conc, s.step_conc_override
    )
    if not np.all(np.isfinite(conc)) and len(steps):
        warnings.append("Enter stock concentration, initial cell volume and addition volumes (or concentration overrides).")

    signal = polarity * levels - s.background
    if s.dilution_signal and s.v0_ml > 0:
        cum = np.concatenate([[0.0], np.cumsum([float(v or 0) for v in v_ul])])
        signal = signal * (s.v0_ml * 1000.0 + cum) / (s.v0_ml * 1000.0)

    included = np.array([s.include_baseline] + [bool(x) for x in s.step_included], dtype=bool)

    res = CAResult(
        step_times=steps, seg_bounds=seg_bounds, win_bounds=win_bounds,
        plateau=plateau, plateau_sd=plateau_sd, delta=delta, signal=signal,
        conc_added=conc, included=included, polarity=polarity, warnings=warnings,
    )

    # --- fit
    mask = included & np.isfinite(conc) & np.isfinite(signal)
    fit = linear_fit(conc[mask], signal[mask]) if mask.sum() >= 2 else None
    res.fit = fit
    if fit is None:
        return res

    if mask.sum() == 2:
        warnings.append("Only 2 points in the fit: no uncertainty estimates.")
    if fit.slope <= 0:
        warnings.append("Fitted slope is not positive: check the included points and polarity.")

    # --- sigma, LOD / LOQ, sensitivity
    sigma_b = (
        baseline_sigma(t, i, steps[0], s.baseline_window, guard) if len(steps) else NAN
    )
    sigma = fit.s_yx if s.sigma_source == "fit" else sigma_b
    if not math.isfinite(sigma):
        sigma = fit.s_yx if math.isfinite(fit.s_yx) else NAN
        if s.sigma_source != "fit":
            warnings.append("Baseline too short for σ; using the fit's residual std. dev.")
    res.sigma = sigma
    m = abs(fit.slope)
    res.sensitivity = m
    res.sensitivity_area = m / s.area_cm2 if s.area_cm2 > 0 else NAN
    if m > 0 and math.isfinite(sigma):
        res.lod = 3.0 * sigma / m
        res.loq = 10.0 * sigma / m

    # --- standard addition
    if fit.slope != 0:
        res.c_x = fit.intercept / fit.slope
        res.c_x_se = fit.x_intercept_se
        v_sample = s.v_sample_ml if s.v_sample_ml > 0 else s.v0_ml
        res.c_sample = res.c_x * (s.v0_ml / v_sample) if v_sample > 0 else res.c_x
    if not s.include_baseline:
        warnings.append("Baseline point excluded: the x-intercept is extrapolated from the additions only.")
    return res
