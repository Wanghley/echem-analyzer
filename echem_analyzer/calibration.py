"""Calibration and analytical figures of merit for any sensor response (no Qt).

Fits response vs concentration with a linear, log-linear (Nernstian), Michaelis-Menten or Hill model, finds the
linear range, computes sensitivity, LOD / LOQ and predicts concentrations of unknowns with uncertainty.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

import numpy as np
from scipy import optimize, stats

NAN = float("nan")
LINEAR, LOGLIN, MICHAELIS, HILL = "linear", "loglinear", "michaelis", "hill"
MODEL_LABELS = {LINEAR: "Linear", LOGLIN: "Log-linear (Nernstian)", MICHAELIS: "Michaelis–Menten / Langmuir",
                HILL: "Hill"}


@dataclass
class Param:
    name: str
    value: float
    se: float = NAN


@dataclass
class ModelFit:
    model: str
    params: list[Param]
    x: np.ndarray
    y: np.ndarray
    r2: float = NAN
    adj_r2: float = NAN
    rmse: float = NAN
    aic: float = NAN
    ok: bool = True
    message: str = ""
    _predict: Optional[Callable[[np.ndarray], np.ndarray]] = field(default=None, repr=False)

    def p(self, name: str) -> float:
        return next((q.value for q in self.params if q.name == name), NAN)

    def se(self, name: str) -> float:
        return next((q.se for q in self.params if q.name == name), NAN)

    def predict(self, x) -> np.ndarray:
        return self._predict(np.asarray(x, float)) if self._predict else np.full(np.shape(x), NAN)

    @property
    def residuals(self) -> np.ndarray:
        return self.y - self.predict(self.x)


def _clean(x, y):
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    return x[ok], y[ok]


def _stats(y: np.ndarray, yhat: np.ndarray, k: int) -> tuple[float, float, float, float]:
    n = len(y)
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else NAN
    adj = 1.0 - (1.0 - r2) * (n - 1) / (n - k - 1) if n > k + 1 and math.isfinite(r2) else NAN
    rmse = math.sqrt(ss_res / n) if n else NAN
    aic = n * math.log(max(ss_res, 1e-300) / n) + 2 * k if n else NAN
    return r2, adj, rmse, aic


def _failed(model: str, x, y, msg: str) -> ModelFit:
    return ModelFit(model, [], np.asarray(x, float), np.asarray(y, float), ok=False, message=msg)


# --------------------------------------------------------------------------- models

def fit_linear(x, y, weights: Optional[Sequence[float]] = None) -> ModelFit:
    """y = a + b·x (ordinary or weighted least squares) with parameter standard errors."""
    x, y = _clean(x, y)
    n = len(x)
    if n < 2 or np.ptp(x) == 0:
        return _failed(LINEAR, x, y, "needs at least two distinct concentrations")
    w = np.ones(n) if weights is None else np.asarray(weights, float)[: n]
    xm, ym = np.average(x, weights=w), np.average(y, weights=w)
    sxx = float(np.sum(w * (x - xm) ** 2))
    b = float(np.sum(w * (x - xm) * (y - ym)) / sxx)
    a = ym - b * xm
    yhat = a + b * x
    r2, adj, rmse, aic = _stats(y, yhat, 2)
    if n > 2:
        s2 = float(np.sum(w * (y - yhat) ** 2) / (n - 2))
        se_b, se_a = math.sqrt(s2 / sxx), math.sqrt(s2 * (1.0 / np.sum(w) + xm ** 2 / sxx))
    else:
        se_a = se_b = NAN
    return ModelFit(LINEAR, [Param("intercept", a, se_a), Param("slope", b, se_b)], x, y, r2, adj, rmse, aic,
                    _predict=lambda xx: a + b * xx)


def fit_loglinear(x, y) -> ModelFit:
    """y = a + b·log10(x): potentiometric sensors (b is the Nernstian slope per decade)."""
    x, y = _clean(x, y)
    keep = x > 0
    if keep.sum() < 2:
        return _failed(LOGLIN, x, y, "needs at least two positive concentrations")
    lx = np.log10(x[keep])
    f = fit_linear(lx, y[keep])
    if not f.ok:
        return _failed(LOGLIN, x, y, f.message)
    a, b = f.p("intercept"), f.p("slope")
    return ModelFit(LOGLIN, [Param("intercept", a, f.se("intercept")), Param("slope", b, f.se("slope"))], x[keep], y[keep],
                    f.r2, f.adj_r2, f.rmse, f.aic, _predict=lambda xx: a + b * np.log10(np.maximum(xx, 1e-300)))


def _nl(model: str, func, p0, bounds, x, y, names) -> ModelFit:
    x, y = _clean(x, y)
    k = len(names)
    if len(x) < k + 1:
        return _failed(model, x, y, f"needs at least {k + 1} points")
    try:
        popt, pcov = optimize.curve_fit(func, x, y, p0=p0, bounds=bounds, maxfev=20000)
    except (RuntimeError, ValueError) as exc:
        return _failed(model, x, y, f"fit did not converge ({exc})")
    se = np.sqrt(np.diag(pcov)) if np.all(np.isfinite(pcov)) else np.full(k, NAN)
    r2, adj, rmse, aic = _stats(y, func(x, *popt), k)
    return ModelFit(model, [Param(n_, float(v), float(e)) for n_, v, e in zip(names, popt, se)], x, y, r2, adj, rmse,
                    aic, _predict=lambda xx, p=tuple(popt): func(xx, *p))


def fit_michaelis(x, y) -> ModelFit:
    """y = ymax·x / (K + x): saturating (enzyme / Langmuir) response; K is the apparent Km (or 1/affinity)."""
    xx, yy = _clean(x, y)
    if len(xx) < 3:
        return _failed(MICHAELIS, xx, yy, "needs at least 3 points")
    ymax0 = float(np.max(np.abs(yy))) * 1.5 or 1.0
    k0 = float(np.median(xx[xx > 0])) if np.any(xx > 0) else 1.0
    sign = 1.0 if np.mean(yy) >= 0 else -1.0
    lo, hi = (0, np.inf) if sign > 0 else (-np.inf, 0)
    f = lambda c, ymax, K: ymax * c / (K + c)                                           # noqa: E731
    return _nl(MICHAELIS, f, [sign * ymax0, k0], ([lo, 1e-12], [hi, np.inf]), xx, yy, ["ymax", "K"])


def fit_hill(x, y) -> ModelFit:
    """y = ymax·x^n / (K^n + x^n): cooperative / non-ideal binding (affinity sensors)."""
    xx, yy = _clean(x, y)
    if len(xx) < 4:
        return _failed(HILL, xx, yy, "needs at least 4 points")
    pos = xx[xx > 0]
    ymax0 = float(np.max(np.abs(yy))) * 1.3 or 1.0
    k0 = float(np.median(pos)) if pos.size else 1.0
    sign = 1.0 if np.mean(yy) >= 0 else -1.0
    lo, hi = (0, np.inf) if sign > 0 else (-np.inf, 0)
    f = lambda c, ymax, K, n: ymax * np.power(np.maximum(c, 0), n) / (K ** n + np.power(np.maximum(c, 0), n))   # noqa: E731
    return _nl(HILL, f, [sign * ymax0, k0, 1.0], ([lo, 1e-12, 0.2], [hi, np.inf, 6.0]), xx, yy, ["ymax", "K", "n"])


def fit_all(x, y) -> dict[str, ModelFit]:
    return {LINEAR: fit_linear(x, y), LOGLIN: fit_loglinear(x, y), MICHAELIS: fit_michaelis(x, y), HILL: fit_hill(x, y)}


def best_model(fits: dict[str, ModelFit]) -> Optional[str]:
    """Lowest AIC among converged fits that have at least one spare degree of freedom."""
    cand = [(f.aic, k) for k, f in fits.items() if f.ok and math.isfinite(f.aic) and len(f.x) > len(f.params) + 0]
    return min(cand)[1] if cand else None


# --------------------------------------------------------------------------- linear range and figures of merit

def linear_range(x, y, r2_min: float = 0.99, dev_max: float = 0.10, min_points: int = 3) -> tuple[float, float, int]:
    """Widest run of consecutive concentration levels that is still linear.

    Levels are the sorted distinct concentrations (replicates averaged).  A run qualifies if its linear fit has
    R² >= ``r2_min`` and no level deviates from the line by more than ``dev_max`` of its response.  Returns
    ``(c_low, c_high, n_levels)`` or ``(nan, nan, 0)``.
    """
    x, y = _clean(x, y)
    levels = np.unique(x)
    if levels.size < min_points:
        return NAN, NAN, 0
    means = np.array([y[x == c].mean() for c in levels])
    best = (NAN, NAN, 0)
    for i in range(levels.size):
        for j in range(i + min_points, levels.size + 1):
            xs, ys = levels[i:j], means[i:j]
            f = fit_linear(xs, ys)
            if not f.ok or not (f.r2 >= r2_min) or f.p("slope") == 0:
                continue
            dev = np.abs(ys - f.predict(xs)) / np.maximum(np.abs(ys), 1e-12)
            if dev.max() <= dev_max and (j - i) > best[2]:
                best = (float(xs[0]), float(xs[-1]), j - i)
    return best


@dataclass
class FiguresOfMerit:
    sensitivity: float = NAN
    sensitivity_se: float = NAN
    intercept: float = NAN
    r2: float = NAN
    sigma: float = NAN
    sigma_source: str = ""
    lod: float = NAN
    loq: float = NAN
    range_low: float = NAN
    range_high: float = NAN
    n_range: int = 0
    n_points: int = 0
    fit: Optional[ModelFit] = None
    warnings: list[str] = field(default_factory=list)


def figures_of_merit(x, y, blank: Optional[Sequence[float]] = None, k_lod: float = 3.0, k_loq: float = 10.0,
                     sigma_from: str = "auto", restrict_to_range: bool = True, r2_min: float = 0.99,
                     dev_max: float = 0.10) -> FiguresOfMerit:
    """Sensitivity, LOD, LOQ and linear range from a calibration.

    ``sigma_from``: ``"blank"`` (SD of blank responses), ``"residual"`` (SD of the linear fit's residuals) or
    ``"auto"`` (the blank if at least 3 blank values are given, else the residuals).  The sensitivity is the slope
    of the linear part (the detected linear range when ``restrict_to_range``).
    """
    x, y = _clean(x, y)
    out = FiguresOfMerit(n_points=len(x))
    if len(np.unique(x)) < 2:
        out.warnings.append("A calibration needs at least two different concentrations.")
        return out
    lo, hi, nlev = linear_range(x, y, r2_min=r2_min, dev_max=dev_max)
    out.range_low, out.range_high, out.n_range = lo, hi, nlev
    sel = (x >= lo) & (x <= hi) if (restrict_to_range and nlev) else np.ones(len(x), bool)
    if restrict_to_range and not nlev:
        out.warnings.append("No linear range met the criteria (R² ≥ %.3g, deviation ≤ %.0f %%); using all points." % (r2_min, dev_max * 100))
    f = fit_linear(x[sel], y[sel])
    out.fit = f
    if not f.ok:
        out.warnings.append(f.message)
        return out
    out.sensitivity, out.sensitivity_se = f.p("slope"), f.se("slope")
    out.intercept, out.r2 = f.p("intercept"), f.r2
    blank = np.asarray([b for b in (blank if blank is not None else []) if np.isfinite(b)], float)
    use_blank = sigma_from == "blank" or (sigma_from == "auto" and blank.size >= 3)
    if use_blank:
        if blank.size < 2:
            out.warnings.append("Need at least two blank measurements for a blank-based σ.")
        else:
            out.sigma, out.sigma_source = float(np.std(blank, ddof=1)), f"blank (n = {blank.size})"
    if not math.isfinite(out.sigma):
        n = int(sel.sum())
        if n > 2:
            out.sigma, out.sigma_source = float(np.sqrt(np.sum(f.residuals ** 2) / (n - 2))), "fit residuals"
    if math.isfinite(out.sigma) and out.sensitivity:
        out.lod = k_lod * out.sigma / abs(out.sensitivity)
        out.loq = k_loq * out.sigma / abs(out.sensitivity)
    elif not math.isfinite(out.sigma):
        out.warnings.append("σ could not be estimated (too few points): LOD / LOQ unavailable.")
    if out.sensitivity <= 0 and np.mean(y) > 0:
        out.warnings.append("The sensitivity is not positive.")
    return out


# --------------------------------------------------------------------------- prediction

def predict_concentration(fit: ModelFit, y0: float, n_rep: int = 1) -> tuple[float, float]:
    """Concentration for a measured response ``y0`` and its standard uncertainty (NaN outside the model's range).

    Linear: the textbook inverse-prediction formula (Harris).  Other models: the analytic inverse, with the
    uncertainty propagated from the residual scatter through the local slope.
    """
    if not fit.ok or not math.isfinite(y0):
        return NAN, NAN
    n = len(fit.x)
    if fit.model == LINEAR:
        a, b = fit.p("intercept"), fit.p("slope")
        if b == 0:
            return NAN, NAN
        x0 = (y0 - a) / b
        if n > 2:
            sxx = float(np.sum((fit.x - fit.x.mean()) ** 2))
            s = math.sqrt(float(np.sum(fit.residuals ** 2)) / (n - 2))
            se = s / abs(b) * math.sqrt(1.0 / n_rep + 1.0 / n + (y0 - fit.y.mean()) ** 2 / (b ** 2 * sxx))
        else:
            se = NAN
        return float(x0), float(se)
    if fit.model == LOGLIN:
        a, b = fit.p("intercept"), fit.p("slope")
        x0 = 10 ** ((y0 - a) / b) if b else NAN
    elif fit.model == MICHAELIS:
        ymax, K = fit.p("ymax"), fit.p("K")
        x0 = K * y0 / (ymax - y0) if (ymax - y0) * ymax > 0 and y0 * ymax >= 0 else NAN
    elif fit.model == HILL:
        ymax, K, nn = fit.p("ymax"), fit.p("K"), fit.p("n")
        r = y0 / (ymax - y0) if (ymax - y0) != 0 else NAN
        x0 = K * r ** (1.0 / nn) if r == r and r > 0 else NAN
    else:
        return NAN, NAN
    if not math.isfinite(x0):
        return NAN, NAN
    h = max(abs(x0) * 1e-4, 1e-12)
    slope = (fit.predict(x0 + h) - fit.predict(x0 - h)) / (2 * h)
    se = (fit.rmse * math.sqrt(n / max(n - len(fit.params), 1)) / abs(slope)) if slope else NAN
    return float(x0), float(se)


def replicate_summary(x, y) -> list[tuple[float, float, float, int]]:
    """(concentration, mean, sd, n) for every distinct concentration (sd is NaN for n = 1)."""
    x, y = _clean(x, y)
    out = []
    for c in np.unique(x):
        v = y[x == c]
        out.append((float(c), float(v.mean()), float(v.std(ddof=1)) if v.size > 1 else NAN, int(v.size)))
    return out


def nernst_slope(z: int = 1, temp_c: float = 25.0) -> float:
    """Theoretical Nernstian slope in mV per decade for a species of charge ``z`` at ``temp_c``."""
    return 1000.0 * math.log(10) * 8.314462618 * (temp_c + 273.15) / (abs(z) * 96485.33212)
