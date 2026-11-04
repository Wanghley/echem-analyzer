"""General standard-addition analysis (no Qt): units, the fit, its uncertainty, LOD / LOQ and the sanity checks.

``ca_analysis.analyze_ca`` extracts one plateau per addition from a chronoamperogram and hands the points to
:func:`evaluate_points`; any other part of the app (peak heights from DPV additions, EIS, a manual table) can call the same
function with its own (added concentration, signal) arrays, so every standard-addition result in the app is computed one way.

Model.  After n additions of volume v from a stock c_s into a cell of volume V0::

    C_n = c_s·n·v / (V0 + n·v)                       (concentration mode)
    y_n = signal_n − I_bkg,   y = a + b·x   with x = C_n
    x0 = −a/b,   C_x = |x0|,   C_sample = C_x·V0/V_sample

Exactly one dilution correction is ever applied (``dilution_mode``): "concentration" as above, "signal" (x = c_s·n·v/V0 and
y scaled by (V0 + n·v)/V0, the algebraically exact form), or "none".  They are alternatives, never additive.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np
from scipy import stats

from .ca_analysis import linear_fit
from .models import CAAnalysisSettings, FitResult

NAN = float("nan")

# Stored quantities are always mL, µL and µA; a unit selector only changes how they are shown.
VOLUME_ML = {"µL": 1e-3, "mL": 1.0}               # factors to mL (V0, V_sample)
VOLUME_UL = {"µL": 1.0, "mL": 1000.0}             # factors to µL (volume per addition)
CURRENT_UA = {"nA": 1e-3, "µA": 1.0, "mA": 1e3}
STOCK_UNITS = ("M", "mM", "µM", "nM", "pM")
DILUTION_MODES = ("concentration", "signal", "none")
SIGMA_SOURCES = ("fit", "baseline", "blank")
SIGMA_LABELS = {"fit": "residual s(y/x)", "baseline": "baseline noise", "blank": "blank replicates"}
DILUTION_LABELS = {
    "concentration": "Concentration: C = c·nv/(V₀+nv)",
    "signal": "Signal scaling: y·(V₀+ΣV)/V₀",
    "none": "None",
}


def factor(unit: str, table: dict[str, float]) -> float:
    """How many canonical units one ``unit`` is. An unknown unit is an error: units are never guessed."""
    try:
        return table[unit]
    except KeyError:
        raise ValueError(f"unknown unit {unit!r} (expected one of {', '.join(table)})") from None


def to_canonical(value: float, unit: str, table: dict[str, float]) -> float:
    return value * factor(unit, table)


def from_canonical(value: float, unit: str, table: dict[str, float]) -> float:
    return value / factor(unit, table)


def dilution_flags(mode: str) -> tuple[bool, bool]:
    """(correct the concentration, scale the signal): at most one of the two is True."""
    if mode == "signal":
        return False, True
    return mode == "concentration", False


def volumes_used_ul(s: CAAnalysisSettings) -> list[float]:
    """Volume of every addition (µL). A verified volume replaces the nominal one for additions still at the nominal value;
    a volume you typed for one particular step is kept as typed."""
    vols = [float(v or 0.0) for v in s.step_volumes_ul]
    if s.verified_ul > 0:
        vols = [s.verified_ul if abs(v - s.v_add_ul) < 1e-12 else v for v in vols]
    return vols


def sample_factor(s: CAAnalysisSettings) -> float:
    """V0 / V_sample (1 when no sample volume is given)."""
    v_sample = s.v_sample_ml if s.v_sample_ml > 0 else s.v0_ml
    return s.v0_ml / v_sample if v_sample > 0 and s.v0_ml > 0 else 1.0


# =============================================================================== checks that need only the inputs

def input_warnings(s: CAAnalysisSettings, volumes_ul: Sequence[float], seg_lengths: Sequence[float] = ()) -> list[str]:
    w: list[str] = []
    if s.background == 0:
        w.append("Background current is 0 µA: Cₓ then includes the non-analyte current. Measure a blank at the same potential "
                 "and time and enter it.")
    if s.v_sample_ml > 0 and s.v0_ml > 0 and s.v_sample_ml > s.v0_ml:
        w.append(f"The sample volume ({s.v_sample_ml:g} mL) is larger than the cell volume V₀ ({s.v0_ml:g} mL): the sample "
                 "cannot be more than the whole cell.")
    total = float(sum(volumes_ul))
    ratio = total / (s.v0_ml * 1000.0) if s.v0_ml > 0 else 0.0
    if s.dilution_mode == "none" and ratio > 0.01:
        w.append(f"Dilution correction is off but the additions total {ratio * 100:.1f} % of V₀: the added concentration is "
                 "overestimated. Choose concentration or signal correction.")
    elif s.dilution_mode == "concentration" and ratio > 0.02:
        w.append(f"The additions total {ratio * 100:.1f} % of V₀. Concentration mode corrects the added standard but not the "
                 f"dilution of the sample itself, which biases Cₓ by up to about {ratio * 100:.0f} %; 'Signal scaling' removes it exactly.")
    if s.verified_ul > 0 and s.v_add_ul > 0 and abs(s.verified_ul / s.v_add_ul - 1) > 0.02:
        w.append(f"The verified volume ({s.verified_ul:g} µL) differs from the nominal one ({s.v_add_ul:g} µL) by "
                 f"{(s.verified_ul / s.v_add_ul - 1) * 100:+.1f} %; the verified volume is used.")
    need = s.settle + s.window
    short = [k for k, length in enumerate(seg_lengths) if length < (need if k > 0 else s.window)]
    if short:
        which = ", ".join("baseline" if k == 0 else str(k) for k in short[:4]) + (" …" if len(short) > 4 else "")
        w.append(f"Settle time + averaging window ({s.settle:g} + {s.window:g} = {need:g} s) is longer than the segment(s) {which} "
                 f"(shortest {min(seg_lengths[k] for k in short):.1f} s): the window is cut back or uses the second half of the segment.")
    return w


# =============================================================================== residual pattern

def residual_pattern(res: Sequence[float], s_yx: float) -> Optional[str]:
    """A message when the residuals are not random scatter, else None.

    Two signs of trouble: the ends on one side of the line with the middle on the other (curvature), and a significant
    quadratic term. Both need at least five points."""
    r = np.asarray(res, float)
    n = len(r)
    if n < 5 or not math.isfinite(s_yx) or s_yx <= 0:
        return None
    ends = 0.5 * (r[0] + r[-1])
    mid = float(np.mean(r[1:-1]))
    if r[0] * r[-1] > 0 and ends * mid < 0 and abs(ends) > 1.0 * s_yx:
        return "the first and last points lie on one side of the line and the middle on the other: the response is curved"
    k = np.arange(n, dtype=float)
    k = (k - k.mean()) / (k.std() or 1.0)
    q = k * k - np.mean(k * k)
    q = q - k * (np.sum(q * k) / np.sum(k * k))                 # the part of k² that the straight line cannot absorb
    denom = float(np.sum(q * q))
    if denom > 0 and n > 3:
        c = float(np.sum(q * r) / denom)
        se = s_yx / math.sqrt(denom)
        if se > 0 and abs(c / se) > stats.t.ppf(0.975, n - 3):
            return "the residuals have a significant curvature (quadratic term)"
    return None


# =============================================================================== the evaluation

@dataclass
class Figures:
    """Everything derived from the points (units: signal in µA, concentrations in the stock's unit)."""

    fit: Optional[FitResult] = None
    n_used: int = 0
    n_expected: int = 0
    x0: float = NAN
    c_x: float = NAN
    c_x_se: float = NAN
    c_x_ci95: float = NAN
    c_sample: float = NAN
    c_sample_se: float = NAN
    c_sample_ci95: float = NAN
    suitability: float = NAN
    sensitivity: float = NAN
    sigmas: dict[str, float] = field(default_factory=dict)
    lods: dict[str, float] = field(default_factory=dict)
    loqs: dict[str, float] = field(default_factory=dict)
    sigma: float = NAN
    sigma_used: str = ""
    lod: float = NAN
    loq: float = NAN
    alt: Optional[dict] = None
    warnings: list[str] = field(default_factory=list)


def _x_intercept_block(fit: FitResult, factor_sample: float) -> dict:
    x0 = -fit.intercept / fit.slope if fit.slope != 0 else NAN
    se, ci = fit.x_intercept_se, fit.x_intercept_ci95
    return {"x0": x0, "c_x": abs(x0), "c_x_se": se, "c_x_ci95": ci,
            "c_sample": abs(x0) * factor_sample, "c_sample_se": se * factor_sample, "c_sample_ci95": ci * factor_sample}


def evaluate_points(conc_added: Sequence[float], signal: Sequence[float], included: Sequence[bool], s: CAAnalysisSettings,
                    sigma_baseline: float = NAN, n_additions: Optional[int] = None) -> Figures:
    """Fit signal against added concentration and derive C_x, C_sample, LOD, LOQ and the checks.

    Point 0 is the baseline (no addition). ``signal`` must already be background-subtracted (and scaled in signal mode).
    ``sigma_baseline`` is the noise of one trace (µA), if known.
    """
    x = np.asarray(conc_added, float)
    y = np.asarray(signal, float)
    inc = np.asarray(included, bool)
    n_add = (len(x) - 1) if n_additions is None else n_additions
    out = Figures(n_expected=n_add + (1 if s.include_baseline else 0))
    mask = inc & np.isfinite(x) & np.isfinite(y)
    out.n_used = int(mask.sum())
    fit = linear_fit(x[mask], y[mask]) if out.n_used >= 2 else None
    out.fit = fit
    if fit is None:
        return out
    w = out.warnings
    if out.n_used != out.n_expected:
        w.append(f"The fit uses {out.n_used} points but the run has {n_add} addition(s)"
                 f"{' plus the baseline' if s.include_baseline else ''} ({out.n_expected} expected): some points are unticked or "
                 "have no concentration or signal.")
    if out.n_used == 2:
        w.append("Only 2 points in the fit: no uncertainty estimates.")
    if fit.slope <= 0:
        w.append("Fitted slope is not positive: check the included points and polarity.")
    if not s.include_baseline:
        w.append("Baseline point excluded: the x-intercept is extrapolated from the additions only.")

    # ---- x-intercept, sample concentration
    blk = _x_intercept_block(fit, sample_factor(s))
    for k, v in blk.items():
        setattr(out, k, v)
    if math.isfinite(out.x0) and out.x0 > 0:
        w.append("The x-intercept is at a positive added concentration (negative sample concentration): the signal at zero "
                 "addition is below the line's zero. Check the background and the baseline point.")
    out.suitability = fit.suitability
    if math.isfinite(fit.suitability) and fit.suitability < s.suitability_min:
        w.append(f"Suitability b·√Sxx/s(y) = {fit.suitability:.3g} is below {s.suitability_min:g}: the points do not pin the "
                 "extrapolation down well. Use larger or more additions, or reduce the scatter.")

    # ---- σ from the three sources, LOD / LOQ
    blank = [float(v) for v in s.blank_replicates if v is not None and math.isfinite(float(v))]
    out.sigmas = {"fit": fit.s_yx if math.isfinite(fit.s_yx) else NAN, "baseline": float(sigma_baseline),
                  "blank": float(np.std(blank, ddof=1)) if len(blank) >= 2 else NAN}
    b = abs(fit.slope)
    out.sensitivity = b
    for key, sg in out.sigmas.items():
        out.lods[key] = s.lod_k * sg / b if b > 0 and math.isfinite(sg) else NAN
        out.loqs[key] = s.loq_k * sg / b if b > 0 and math.isfinite(sg) else NAN
    want = s.sigma_source if s.sigma_source in SIGMA_SOURCES else "fit"
    order = [want] + [k for k in SIGMA_SOURCES if k != want]
    used = next((k for k in order if math.isfinite(out.sigmas[k])), "")
    if used and used != want:
        w.append(f"σ from {SIGMA_LABELS[want]} is not available ({'at least 2 blank replicates are needed' if want == 'blank' else 'the trace or the fit is too short'}); "
                 f"using {SIGMA_LABELS[used]}.")
    out.sigma_used = used
    if used:
        out.sigma, out.lod, out.loq = out.sigmas[used], out.lods[used], out.loqs[used]
    lb, lf = out.lods.get("baseline", NAN), out.lods.get("fit", NAN)
    if math.isfinite(lb) and math.isfinite(lf) and lf > 0 and lb < lf / 3.0:
        times = f"{lf / lb:.1f}× smaller" if lb > 0 else "far smaller"
        w.append(f"The LOD from baseline noise ({lb:.3g}) is {times} than the LOD from s(y/x) ({lf:.3g}): the baseline "
                 "noise ignores the scatter between additions. Report the larger value unless the fit is truly that good.")

    # ---- plausibility and residuals
    unit = s.stock_unit
    if math.isfinite(out.c_sample):
        if s.plausible_max > 0 and out.c_sample > s.plausible_max:
            w.append(f"C_sample = {out.c_sample:.3g} {unit} is above the plausible maximum you set ({s.plausible_max:g} {unit}): "
                     "is the stock unit right (mM vs µM)?")
        elif s.stock_conc > 0 and out.c_sample > s.stock_conc:
            w.append(f"C_sample ({out.c_sample:.3g} {unit}) is larger than the stock itself ({s.stock_conc:g} {unit}): check the "
                     "stock concentration and its unit.")
    msg = residual_pattern(fit.residuals, fit.s_yx)
    if msg:
        w.append(f"Residuals show a pattern: {msg}.")

    # ---- the other baseline choice
    if s.compare_baseline and len(x) > 1:
        inc2 = inc.copy()
        inc2[0] = not inc[0]
        m2 = inc2 & np.isfinite(x) & np.isfinite(y)
        f2 = linear_fit(x[m2], y[m2]) if m2.sum() >= 2 else None
        if f2 is not None:
            alt = _x_intercept_block(f2, sample_factor(s))
            alt.update(include_baseline=bool(inc2[0]), n=f2.n, slope=f2.slope, intercept=f2.intercept, r2=f2.r2, s_yx=f2.s_yx)
            out.alt = alt
    return out
