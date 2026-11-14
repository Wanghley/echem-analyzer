"""Ion-selective electrodes: activity, the Nernstian region and detection limit, and selectivity coefficients (no Qt).

An ISE answers to the *logarithm* of activity, E = E₀ + S·log₁₀ a with S = 2.303·R·T/(z·F), but only above a detection limit;
below it the membrane's own ion leaching and interferents hold the potential at a plateau.  :func:`fit_ise` finds that
Nernstian region in a calibration and the IUPAC detection limit, the concentration where the extrapolated plateau and the
extrapolated Nernstian line cross.  :func:`log_k_ssm` and :func:`log_k_fim` give the selectivity coefficient log Kᵖᵒᵗ of the
Nikolsky–Eisenman equation, E = E₀ + S·log₁₀(aᵢ + Kᵖᵒᵗ·aⱼ^(zᵢ/zⱼ)), by the separate-solution and fixed-interference methods.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .calibration import ModelFit, fit_loglinear, nernst_slope

NAN = float("nan")
R_GAS, F_FARADAY = 8.314462618, 96485.33212
MIN_LEVELS_FOR_PLATEAU = 5            # two levels for the plateau and three for the Nernstian line
SINGLE_LINE_R2 = 0.99999              # a calibration this straight has no plateau worth looking for
FLAT_FRACTION = 0.5                   # the plateau's slope must be below this fraction of the Nernstian one
AIC_MARGIN = 4.0                      # how much better (in AIC) two segments must fit than one, beyond their extra parameters
TRIM_MV = 1.0                         # a point this far from the line through the others is on the curved corner, not on the line
TRIM_SIGMAS = 3.0                     # ... or this many standard deviations of the scatter, whichever is larger


def relative_permittivity_water(temp_c: float) -> float:
    """Static relative permittivity of water (Malmberg–Maryott fit, 0–100 °C): 78.3 at 25 °C."""
    return 87.74 - 0.40008 * temp_c + 9.398e-4 * temp_c ** 2 - 1.41e-6 * temp_c ** 3


def debye_huckel_a(temp_c: float = 25.0) -> float:
    """The Debye–Hückel constant A of log₁₀γ = −A·z²·f(I), about 0.51 at 25 °C."""
    return 1.8248e6 * (relative_permittivity_water(temp_c) * (temp_c + 273.15)) ** -1.5


def activity_coefficient(z: int, ionic_strength_m: float, temp_c: float = 25.0) -> float:
    """Davies equation: log₁₀γ = −A·z²·(√I/(1+√I) − 0.3·I).  Good to about I = 0.5 M; 1 when I = 0."""
    if ionic_strength_m < 0:
        raise ValueError("ionic strength cannot be negative")
    root = math.sqrt(ionic_strength_m)
    return 10.0 ** (-debye_huckel_a(temp_c) * z * z * (root / (1.0 + root) - 0.3 * ionic_strength_m))


# --------------------------------------------------------------------------- calibration

@dataclass
class ISEFit:
    """E = intercept + slope·log₁₀(c) over the Nernstian region, and what lies outside it."""
    slope: float = NAN                    # mV per decade
    slope_se: float = NAN
    intercept: float = NAN                # mV at c = 1 (in the concentration unit used)
    r2: float = NAN
    nernst: float = NAN                   # the theoretical slope for this charge and temperature, mV/decade (positive)
    slope_pct: float = NAN                # |slope| as a percentage of it
    index: np.ndarray = field(default_factory=lambda: np.array([], int))      # points of the input that make up the Nernstian region
    c_low: float = NAN                    # the concentrations that region spans
    c_high: float = NAN
    lod: float = NAN                      # IUPAC detection limit, in the concentration unit used
    lod_found: bool = False
    plateau: Optional[tuple[float, float]] = None        # (slope, intercept) of the flat part below the detection limit
    saturation_above: float = NAN         # set when the response flattens at the high end instead
    fit: Optional[ModelFit] = None        # the log-linear fit of the Nernstian region (x = concentration)
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.fit is not None and self.fit.ok


def _sse(u: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    slope, intercept = np.polyfit(u, y, 1)
    return float(np.sum((y - (slope * u + intercept)) ** 2)), float(slope), float(intercept)


def _trim(u: np.ndarray, y: np.ndarray, mask: np.ndarray, end: str, min_levels: int) -> np.ndarray:
    """Drop the levels at one end of a straight segment while they sit off the line through the rest.

    The transition between the plateau and the Nernstian line is a smooth corner, and the points on it bend both lines toward
    each other (a true 59 mV/decade slope reads about 57).  Each end level is compared with the line fitted to the others; it
    goes when it is farther from it than ``TRIM_MV`` or ``TRIM_SIGMAS`` times the scatter of those others.  ``end`` is "low" or
    "high" (the end next to the corner); at least ``min_levels`` levels are always kept.
    """
    mask = mask.copy()
    while True:
        lev = np.unique(np.round(u[mask], 9))
        if len(lev) <= min_levels:
            return mask
        edge = lev[0] if end == "low" else lev[-1]
        at_edge = mask & (np.abs(u - edge) < 1e-9)
        rest = mask & ~at_edge
        if rest.sum() < 2:
            return mask
        slope, intercept = np.polyfit(u[rest], y[rest], 1)
        resid = y[rest] - (slope * u[rest] + intercept)
        scatter = float(np.sqrt(np.sum(resid ** 2) / (rest.sum() - 2))) if rest.sum() > 2 else 0.0
        deviation = abs(float(np.mean(y[at_edge])) - (slope * float(np.mean(u[at_edge])) + intercept))
        if deviation <= max(TRIM_MV, TRIM_SIGMAS * scatter):
            return mask
        mask = rest


def fit_ise(conc, e_mv, z: int = 1, temp_c: float = 25.0) -> ISEFit:
    """Find the Nernstian region of an ISE calibration and its detection limit.

    ``conc`` is any concentration (or activity) unit; the intercept and limits are in that unit.  With five or more
    concentration levels the curve is tried as two straight lines in log₁₀c; the split is kept when it fits clearly better than
    one line (AIC) and one side is flat compared with the other.  If the flat side is at low concentration the detection limit
    is where the two lines cross (IUPAC); if it is at high concentration the response is saturating and the Nernstian region
    ends there.  Otherwise the whole curve is the Nernstian region and the detection limit lies below the lowest standard.
    """
    out = ISEFit(nernst=nernst_slope(abs(z) or 1, temp_c))
    c, y = np.asarray(conc, float), np.asarray(e_mv, float)
    ok = np.isfinite(c) & np.isfinite(y) & (c > 0)
    idx = np.flatnonzero(ok)
    idx = idx[np.argsort(c[idx], kind="stable")]
    u, yy = np.log10(c[idx]), y[idx]
    levels = np.unique(np.round(u, 9))
    if len(levels) < 3:
        out.message = "An electrode calibration needs at least three different positive concentrations."
        return out

    keep = np.ones(len(idx), bool)
    sse1, s1, b1 = _sse(u, yy)
    n = len(u)
    r2_single = 1.0 - sse1 / float(np.sum((yy - yy.mean()) ** 2)) if np.ptp(yy) > 0 else 1.0
    if len(levels) >= MIN_LEVELS_FOR_PLATEAU and r2_single < SINGLE_LINE_R2:
        best = None
        for j in range(2, len(levels) - 2):                      # two levels below the split at least, three above
            edge = levels[j] - 1e-9
            lower, upper = u < edge, u >= edge
            sl, a_l, b_l = _sse(u[lower], yy[lower])
            su, a_u, b_u = _sse(u[upper], yy[upper])
            if best is None or sl + su < best[0]:
                best = (sl + su, lower, upper, (a_l, b_l), (a_u, b_u))
        sse2, lower, upper, (a_l, b_l), (a_u, b_u) = best
        aic1 = n * math.log(max(sse1, 1e-300) / n) + 2 * 2
        aic2 = n * math.log(max(sse2, 1e-300) / n) + 2 * 4
        steep_is_upper = abs(a_u) >= abs(a_l)
        flat, steep = (a_l, a_u) if steep_is_upper else (a_u, a_l)
        if aic2 + AIC_MARGIN < aic1 and abs(flat) < FLAT_FRACTION * abs(steep):
            if steep_is_upper:                                   # flat at low concentration: a detection limit
                upper = _trim(u, yy, upper, "low", 3)            # leave the corner out of both lines
                lower = _trim(u, yy, lower, "high", 2)
                (_, a_u, b_u), (_, a_l, b_l) = _sse(u[upper], yy[upper]), _sse(u[lower], yy[lower])
                keep = upper
                out.plateau = (a_l, b_l)
                u_lod = (b_l - b_u) / (a_u - a_l)
                if levels[0] - 1.0 <= u_lod <= u[upper].min():
                    out.lod, out.lod_found = 10.0 ** u_lod, True
                else:
                    out.message = "The two straight parts do not cross inside the range measured: no detection limit can be read off."
            else:                                                # flat at high concentration: the electrode saturates
                keep = _trim(u, yy, lower, "high", 3)
                out.saturation_above = float(10.0 ** u[keep].max())
                out.message = f"The response flattens above about {out.saturation_above:.3g}: the Nernstian region ends there."
    if not out.lod_found and out.plateau is None and np.isnan(out.saturation_above):
        out.message = "The response is straight down to the lowest standard: the detection limit is below the range measured."
        keep = _trim(u, yy, keep, "low", 3)

    seg = idx[keep]
    fit = fit_loglinear(c[seg], y[seg])
    if not fit.ok or len(fit.x) < 3:
        out.message = "The Nernstian region has fewer than three points: the slope cannot be established."
        return out
    out.fit, out.index = fit, seg
    out.slope, out.slope_se, out.intercept, out.r2 = fit.p("slope"), fit.se("slope"), fit.p("intercept"), fit.r2
    out.slope_pct = abs(out.slope) / out.nernst * 100.0
    out.c_low, out.c_high = float(c[seg].min()), float(c[seg].max())
    return out


def slope_remarks(fit: ISEFit, z: int, temp_c: float) -> list[str]:
    """Plain-language remarks on a fitted slope: its sign, and how it compares with the theoretical one."""
    if not fit.ok:
        return []
    remarks = []
    if (fit.slope > 0) != (z > 0):
        remarks.append(f"The slope is {'positive' if fit.slope > 0 else 'negative'}, but a {'cation' if z > 0 else 'anion'} gives a "
                       f"{'positive' if z > 0 else 'negative'} one: check the charge or the electrode connection.")
    if fit.slope_pct < 85:
        remarks.append(f"The slope is {fit.slope_pct:.0f} % of Nernstian ({abs(fit.slope):.1f} against {fit.nernst:.1f} mV/decade for z = {z:+d} "
                       f"at {temp_c:g} °C): a sub-Nernstian membrane, often an ageing or fouled one, or interference.")
    elif fit.slope_pct > 110:
        remarks.append(f"The slope is {fit.slope_pct:.0f} % of Nernstian ({abs(fit.slope):.1f} against {fit.nernst:.1f} mV/decade): "
                       "super-Nernstian responses usually mean the charge is wrong, or a drifting reference.")
    return remarks


# --------------------------------------------------------------------------- selectivity

def log_k_ssm(e_primary_mv: float, e_interferent_mv: float, a_primary: float, a_interferent: float, z_primary: int, z_interferent: int,
              temp_c: float = 25.0) -> float:
    """log Kᵖᵒᵗ by the separate-solution method: the potential in a solution of the primary ion alone and in one of the interferent alone.

    log K = (E_j − E_i)·zᵢF/(2.303RT) + log aᵢ − (zᵢ/zⱼ)·log aⱼ  (IUPAC; with aᵢ = aⱼ it reduces to the familiar (1 − zᵢ/zⱼ) log aᵢ term).
    Charges are signed; potentials in mV.  A negative result means the electrode prefers the primary ion.
    """
    if min(a_primary, a_interferent) <= 0 or 0 in (z_primary, z_interferent):
        return NAN
    per_mv = z_primary * F_FARADAY / (math.log(10) * R_GAS * (temp_c + 273.15)) / 1000.0
    return (e_interferent_mv - e_primary_mv) * per_mv + math.log10(a_primary) - (z_primary / z_interferent) * math.log10(a_interferent)


def log_k_fim(fit: ISEFit, a_interferent: float, z_primary: int, z_interferent: int) -> float:
    """log Kᵖᵒᵗ by the fixed-interference method from a calibration in a constant background of the interferent.

    The Nernstian line meets the interference plateau where aᵢ = Kᵖᵒᵗ·aⱼ^(zᵢ/zⱼ), so K = a(crossing)/aⱼ^(zᵢ/zⱼ).  The calibration must
    be of the primary ion's *activity* (or of its concentration at constant ionic strength) in that background.
    """
    if not fit.lod_found or a_interferent <= 0 or 0 in (z_primary, z_interferent):
        return NAN
    return math.log10(fit.lod) - (z_primary / z_interferent) * math.log10(a_interferent)
