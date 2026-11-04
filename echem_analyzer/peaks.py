"""Peak analysis for pulse and sweep voltammetry: DPV, SWV, NPV, LSV and single CV scans (no Qt)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.signal import savgol_filter

NAN = float("nan")


@dataclass
class PeakParams:
    window: Optional[tuple[float, float]] = None     # potential window [lo, hi] searched; None = whole curve
    baseline: str = "linear"                          # "linear" | "poly" | "none"
    poly_order: int = 2
    anchor_frac: float = 0.15                         # fraction of the window at each end used as baseline anchors
    polarity: str = "auto"                            # "auto" | "up" (anodic) | "down" (cathodic)
    smooth: int = 0                                   # Savitzky–Golay window in points (0 = off)


@dataclass
class PeakResult:
    found: bool = False
    e_peak: float = NAN
    height: float = NAN                               # baseline-corrected, signed
    height_raw: float = NAN
    fwhm: float = NAN
    area: float = NAN                                 # ∫ corrected current dE over the peak (µA·V)
    polarity: str = ""
    e: np.ndarray = field(default_factory=lambda: np.array([]))
    i: np.ndarray = field(default_factory=lambda: np.array([]))            # raw current inside the window
    baseline: np.ndarray = field(default_factory=lambda: np.array([]))
    corrected: np.ndarray = field(default_factory=lambda: np.array([]))
    warnings: list[str] = field(default_factory=list)


def _baseline(e: np.ndarray, i: np.ndarray, p: PeakParams) -> np.ndarray:
    if p.baseline == "none" or len(e) < 4:
        return np.zeros_like(i)
    k = max(2, int(round(p.anchor_frac * len(e))))
    idx = np.r_[0:k, len(e) - k:len(e)]
    if p.baseline == "poly":
        order = int(min(max(p.poly_order, 1), len(idx) - 1))
        return np.polyval(np.polyfit(e[idx], i[idx], order), e)
    # linear: through the mean point of each end so a noisy single sample cannot tilt it
    (x0, y0), (x1, y1) = (e[:k].mean(), i[:k].mean()), (e[-k:].mean(), i[-k:].mean())
    return y0 + (y1 - y0) * (e - x0) / (x1 - x0) if x1 != x0 else np.full_like(i, y0)


def _fwhm(e: np.ndarray, c: np.ndarray, k: int) -> float:
    """Full width at half height of the (signed) peak at index ``k`` by linear interpolation."""
    half = c[k] / 2.0
    sign = 1.0 if c[k] >= 0 else -1.0

    def cross(rng) -> float:
        for a, b in rng:
            if sign * (c[a] - half) >= 0 > sign * (c[b] - half):
                return float(e[a] + (half - c[a]) * (e[b] - e[a]) / (c[b] - c[a]))
        return NAN

    left = cross(((j, j - 1) for j in range(k, 0, -1)))
    right = cross(((j, j + 1) for j in range(k, len(e) - 1)))
    return abs(right - left) if math.isfinite(left) and math.isfinite(right) else NAN


def analyze_peak(e, i, params: PeakParams = PeakParams()) -> PeakResult:
    """Find the dominant peak in the window, with a baseline under it."""
    e = np.asarray(e, float)
    i = np.asarray(i, float)
    ok = np.isfinite(e) & np.isfinite(i)
    e, i = e[ok], i[ok]
    res = PeakResult()
    if len(e) < 5:
        res.warnings.append("Too few points.")
        return res
    order = np.argsort(e)
    e, i = e[order], i[order]
    if params.window:
        lo, hi = sorted(params.window)
        m = (e >= lo) & (e <= hi)
        e, i = e[m], i[m]
    if len(e) < 6:
        res.warnings.append("The window contains too few points.")
        return res
    y = savgol_filter(i, params.smooth if params.smooth % 2 else params.smooth + 1, 3) \
        if params.smooth and params.smooth > 4 and params.smooth < len(i) else i
    base = _baseline(e, y, params)
    c = y - base
    if params.polarity == "up":
        k = int(np.argmax(c))
    elif params.polarity == "down":
        k = int(np.argmin(c))
    else:
        k = int(np.argmax(np.abs(c)))
    pol = "anodic (up)" if c[k] >= 0 else "cathodic (down)"
    res.found = True
    res.e, res.i, res.baseline, res.corrected = e, i, base, c
    res.e_peak, res.height, res.height_raw, res.polarity = float(e[k]), float(c[k]), float(i[k]), pol
    res.fwhm = _fwhm(e, c, k)
    # integrate only the contiguous stretch around the peak that keeps the peak's sign
    sign = 1.0 if c[k] >= 0 else -1.0
    a = k
    while a > 0 and sign * c[a - 1] > 0:
        a -= 1
    b = k
    while b < len(c) - 1 and sign * c[b + 1] > 0:
        b += 1
    res.area = float(np.trapezoid(c[a:b + 1], e[a:b + 1])) if hasattr(np, "trapezoid") else float(np.trapz(c[a:b + 1], e[a:b + 1]))
    margin = max(2, int(0.05 * len(c)))                 # within 5 % of either end of the window
    if k < margin or k > len(c) - 1 - margin:
        res.warnings.append("The maximum sits at the edge of the window: the peak may be cut off; widen the window.")
    edge = max(abs(c[:2]).max(), abs(c[-2:]).max())
    if abs(c[k]) < 3 * max(np.std(np.diff(c)) / math.sqrt(2), 1e-12) or abs(c[k]) < 1.5 * edge:
        res.warnings.append("The peak is barely above the noise / baseline: treat the height with caution.")
    return res


@dataclass
class SweepFeatures:
    onset: float = NAN              # potential where the current departs from the baseline (tangent intersection)
    half_wave: float = NAN          # potential at half the limiting current
    limiting: float = NAN           # plateau current (mean of the last 10 % of the sweep)
    warnings: list[str] = field(default_factory=list)


def sweep_features(e, i) -> SweepFeatures:
    """Features of a sigmoidal (steady-state) linear sweep: limiting current, E½ and onset potential."""
    e = np.asarray(e, float)
    i = np.asarray(i, float)
    ok = np.isfinite(e) & np.isfinite(i)
    e, i = e[ok], i[ok]
    out = SweepFeatures()
    if len(e) < 20:
        out.warnings.append("Too few points for a sweep analysis.")
        return out
    order = np.argsort(e)
    e, i = e[order], i[order]
    n = max(3, len(e) // 10)
    base0, lim = float(np.mean(i[:n])), float(np.mean(i[-n:]))
    out.limiting = lim - base0
    if abs(out.limiting) < 1e-12:
        out.warnings.append("No change in current across the sweep.")
        return out
    frac = (i - base0) / (lim - base0)
    half = np.flatnonzero((frac[:-1] - 0.5) * (frac[1:] - 0.5) <= 0)
    if half.size:
        j = int(half[0])
        out.half_wave = float(e[j] + (0.5 - frac[j]) * (e[j + 1] - e[j]) / (frac[j + 1] - frac[j])) if frac[j + 1] != frac[j] else float(e[j])
    slope = np.gradient(frac, e)
    k = int(np.argmax(np.abs(slope)))
    # onset = where the tangent at the steepest point meets the baseline level (frac = 0)
    if slope[k] != 0:
        out.onset = float(e[k] - frac[k] / slope[k])
    return out
