"""The sensor study: calibration, reproducibility, stability, selectivity and recovery from a table of responses.

Responses (see :class:`~echem_analyzer.models.Response`) come from any technique; this module does not care where
they came from.  Everything here is Qt-free and drives the Study tab, the reports and the tests.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from . import calibration as cal
from . import potentiometry as pot
from . import studies
from .models import Response

NAN = float("nan")
CONC_FACTORS = {"M": 1.0, "mM": 1e-3, "µM": 1e-6, "uM": 1e-6, "μM": 1e-6, "nM": 1e-9, "pM": 1e-12, "fM": 1e-15}
CONC_UNITS = ["M", "mM", "µM", "nM", "pM", "fM"]
ROLES = ("standard", "blank", "sample")


def cal_g(value: float) -> str:
    return f"{value:.4g}"


def conc_to(value: float, frm: str, to: str) -> float:
    try:
        return value * CONC_FACTORS[frm] / CONC_FACTORS[to]
    except KeyError as exc:
        raise ValueError(f"unknown concentration unit {exc.args[0]!r}") from exc


def usable(responses: Sequence[Response]) -> list[Response]:
    return [r for r in responses if r.use and r.value is not None and math.isfinite(r.value)]


def quantities(responses: Sequence[Response]) -> list[str]:
    return sorted({r.quantity for r in usable(responses)})


def conditions(responses: Sequence[Response], quantity: Optional[str] = None) -> list[str]:
    return sorted({r.condition for r in usable(responses) if quantity in (None, r.quantity)})


def groups(responses: Sequence[Response], quantity: Optional[str] = None) -> list[str]:
    return sorted({r.group for r in usable(responses) if quantity in (None, r.quantity) and r.group})


def pick_unit(responses: Sequence[Response], default: str = "µM") -> str:
    c = Counter(r.conc_unit for r in responses if r.conc is not None and r.conc_unit in CONC_FACTORS)
    return c.most_common(1)[0][0] if c else default


NO_TIME = "none"            # time filter value meaning "only measurements whose time is not set"


def times(responses: Sequence[Response], quantity: Optional[str] = None) -> list[float]:
    return sorted({float(r.time) for r in usable(responses) if r.time is not None and quantity in (None, r.quantity)})


def time_matches(r: Response, time) -> bool:
    """``time`` is None (any), NO_TIME (unset only) or a number (that time)."""
    if time is None:
        return True
    if time == NO_TIME:
        return r.time is None
    return r.time is not None and math.isclose(float(r.time), float(time), rel_tol=1e-9, abs_tol=1e-12)


def select(responses: Sequence[Response], quantity: str, condition: str, roles: Sequence[str] = ("standard",),
           group: Optional[str] = None, time=None) -> list[Response]:
    return [r for r in usable(responses) if r.quantity == quantity and r.condition == condition and r.role in roles
            and (group in (None, "") or r.group == group) and time_matches(r, time)]


# =============================================================================== calibration

@dataclass
class CalibrationOptions:
    quantity: str = ""
    condition: str = "analyte"
    group: Optional[str] = None            # None = pool every electrode
    time: object = None                    # None = all times | NO_TIME | a time value: one calibration session
    model: str = "auto"                    # auto | linear | loglinear | michaelis | hill
    conc_unit: str = ""                    # "" = the most common unit in the data
    sigma_from: str = "auto"               # auto | blank | residual
    k_lod: float = 3.0
    k_loq: float = 10.0
    restrict_range: bool = True
    r2_min: float = 0.99
    dev_max: float = 0.10
    normalise: str = "none"                # none | delta | percent: response as the change from each electrode's own blank
    potentiometric: bool = False           # an ion-selective electrode: Nernstian region, slope vs theory, IUPAC detection limit
    z: int = 1                             # charge of the ion (signed: +1 for Na⁺, −1 for Cl⁻)
    temp_c: float = 25.0                   # temperature the Nernstian slope is judged at
    ionic_strength: float = 0.0            # M, constant background of the standards (0 = treat concentration as activity)


@dataclass
class SampleResult:
    label: str
    response: float
    conc: float = NAN
    se: float = NAN
    expected: float = NAN
    recovery_pct: float = NAN
    group: str = ""
    lo: float = NAN                  # confidence interval of the concentration (see calibration.prediction_interval)
    hi: float = NAN
    ci_method: str = ""


@dataclass
class CalibrationStudy:
    options: CalibrationOptions
    x: np.ndarray = field(default_factory=lambda: np.array([]))
    y: np.ndarray = field(default_factory=lambda: np.array([]))
    groups: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)                  # the response each standard came from
    blanks: np.ndarray = field(default_factory=lambda: np.array([]))
    y_unit: str = ""
    conc_unit: str = ""
    fits: dict[str, cal.ModelFit] = field(default_factory=dict)
    model: str = ""
    fit: Optional[cal.ModelFit] = None
    fom: Optional[cal.FiguresOfMerit] = None
    sensitivity: float = NAN
    sensitivity_unit: str = ""
    lod: float = NAN
    loq: float = NAN
    samples: list[SampleResult] = field(default_factory=list)
    recovery: Optional[studies.RecoverySummary] = None
    ise: Optional[pot.ISEFit] = None                                 # potentiometric mode: the Nernstian region and detection limit
    gamma: float = 1.0                                               # activity coefficient of the ion at the background ionic strength
    e0_mv: float = NAN                                               # standard potential: E at an activity of 1 M
    diagnostics: Optional[cal.PointDiagnostics] = None               # straight-line models only
    fit_index: np.ndarray = field(default_factory=lambda: np.array([], int))     # index into x / y / labels of each point the fit used
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.fit is not None and self.fit.ok

    def flagged_points(self) -> list[tuple[int, str]]:
        """(index into x / y / labels, why) of every standard the diagnostics single out."""
        d = self.diagnostics
        return [(int(self.fit_index[i]), d.reason(i)) for i in d.flagged] if d is not None else []


NORMALISATIONS = {"none": "As measured", "delta": "Change from the blank (Δ)", "percent": "Percent change from the blank (%)"}


def normalise_values(mode: str, standards: Sequence[Response], blanks: Sequence[Response], samples: Sequence[Response],
                     y_unit: str) -> tuple[list[float], list[float], list[float], str, list[str]]:
    """Responses of standards, blanks and samples as the change from the blank of the *same electrode*.

    Label-free and signal-off sensors (Rct of an impedimetric immunosensor, the current of an aptamer sensor) start from a
    different baseline on every electrode, so pooling raw values hides a sensitivity that is the same for all of them.
    ``delta`` subtracts the electrode's mean blank; ``percent`` also divides by it.  An electrode with no blank of its own uses
    the mean of the others' blanks; with no blank at all, or a blank of zero for ``percent``, the values stay as measured.
    Returns (standards, blanks, samples, the new unit, warnings).
    """
    raw = ([r.value for r in standards], [r.value for r in blanks], [r.value for r in samples])
    if mode not in ("delta", "percent"):
        return (*raw, y_unit, [])
    if not blanks:
        return (*raw, y_unit, ["Normalising to the blank needs blank measurements (role “blank”): the responses are used as measured."])
    by_group: dict[str, list[float]] = defaultdict(list)
    for r in blanks:
        by_group[r.group].append(r.value)
    pooled = float(np.mean([r.value for r in blanks]))
    base = {gname: float(np.mean(v)) for gname, v in by_group.items()}
    warnings, missing = [], sorted({r.group for r in (*standards, *samples) if r.group not in base})
    if missing:
        warnings.append("No blank for electrode " + ", ".join(f"“{m or '(unnamed)'}”" for m in missing)
                        + f": the mean of the other blanks ({pooled:.4g} {y_unit}) is used.")
    if mode == "percent" and any(b == 0 for b in [*base.values(), pooled]):
        return (*raw, y_unit, warnings + ["A blank of zero cannot be a reference for a percent change: the responses are used as measured."])

    def apply(r: Response) -> float:
        b = base.get(r.group, pooled)
        return r.value - b if mode == "delta" else 100.0 * (r.value - b) / b

    return ([apply(r) for r in standards], [apply(r) for r in blanks], [apply(r) for r in samples],
            f"Δ{y_unit}" if mode == "delta" else "%", warnings)


def _ise_calibration(out: CalibrationStudy, opts: CalibrationOptions) -> bool:
    """The ion-selective-electrode route: the Nernstian region, the slope against theory and the IUPAC detection limit.

    Fills the study the way a log-linear calibration would (so samples, intervals, diagnostics and the report work unchanged), but
    the fit covers only the Nernstian region and the limits come from where the plateau and the Nernstian line cross.
    """
    ise = pot.fit_ise(out.x, out.y, opts.z, opts.temp_c)
    out.ise = ise
    if not ise.ok:
        out.warnings.append(ise.message or "The electrode calibration could not be fitted.")
        return False
    out.model, out.fit, out.fits = cal.LOGLIN, ise.fit, {cal.LOGLIN: ise.fit}
    out.fit_index = np.asarray(ise.index, int)
    out.gamma = pot.activity_coefficient(opts.z, opts.ionic_strength, opts.temp_c) if opts.ionic_strength > 0 else 1.0
    out.e0_mv = ise.intercept - ise.slope * math.log10(out.gamma * CONC_FACTORS.get(out.conc_unit, 1.0))
    resid = ise.fit.residuals
    sigma, source = (float(np.std(out.blanks, ddof=1)), f"blank (n = {out.blanks.size})") if out.blanks.size >= 3 else \
        (float(np.sqrt(np.sum(resid ** 2) / max(len(resid) - 2, 1))), "fit residuals")
    levels = len(np.unique(np.round(np.log10(out.x[ise.index]), 9)))
    out.fom = cal.FiguresOfMerit(sensitivity=ise.slope, sensitivity_se=ise.slope_se, intercept=ise.intercept, r2=ise.r2, sigma=sigma,
                                 sigma_source=source, lod=ise.lod if ise.lod_found else NAN, range_low=ise.c_low, range_high=ise.c_high,
                                 n_range=levels, n_points=len(out.x), fit=ise.fit)
    out.sensitivity, out.sensitivity_unit = ise.slope, f"{out.y_unit}/decade"
    out.lod = out.fom.lod
    if out.y_unit != "mV":
        out.warnings.append(f"A potentiometric analysis expects potentials in mV or V, but the responses are in {out.y_unit or 'no unit'}: "
                            "the comparison with the Nernstian slope is meaningless.")
    out.warnings += pot.slope_remarks(ise, opts.z, opts.temp_c)
    if ise.message:
        out.warnings.append(ise.message)
    return True


def _ise_sample_remarks(s: SampleResult, ise: pot.ISEFit, unit: str) -> list[str]:
    """What an electrode reading means given where it falls: under the detection limit, in the curved foot, or above the region."""
    where = f"Sample “{s.label}” reads {cal_g(s.conc)} {unit}"
    if ise.lod_found and s.conc < ise.lod:
        return [f"{where}, below the detection limit ({cal_g(ise.lod)} {unit}): report it as below the limit, not as a value."]
    if s.conc < ise.c_low:
        edge = f"the detection limit ({cal_g(ise.lod)}) and " if ise.lod_found else ""
        return [f"{where}, between {edge}the start of the Nernstian region ({cal_g(ise.c_low)} {unit}): the response is curved there, "
                "so the value is only approximate."]
    if s.conc > ise.c_high:
        return [f"{where}, above the Nernstian region (up to {cal_g(ise.c_high)} {unit}): the value is an extrapolation."]
    return []


def run_calibration(responses: Sequence[Response], opts: CalibrationOptions) -> CalibrationStudy:
    out = CalibrationStudy(opts)
    std = [r for r in select(responses, opts.quantity, opts.condition, ("standard",), opts.group, opts.time) if r.conc is not None]
    blanks = select(responses, opts.quantity, opts.condition, ("blank",), opts.group, opts.time)
    samples = select(responses, opts.quantity, opts.condition, ("sample",), opts.group, opts.time)
    out.conc_unit = opts.conc_unit or pick_unit(std + blanks)
    out.y_unit = next((r.unit for r in std), next((r.unit for r in blanks), ""))
    try:
        xs = [conc_to(r.conc, r.conc_unit, out.conc_unit) for r in std]
    except ValueError as exc:
        out.warnings.append(str(exc))
        return out
    std_values, blank_values, sample_values, out.y_unit, norm_warnings = normalise_values(opts.normalise, std, blanks, samples, out.y_unit)
    out.warnings += norm_warnings
    out.x, out.y = np.array(xs, float), np.array(std_values, float)
    out.groups = [r.group for r in std]
    out.labels = [r.source_label or r.id for r in std]
    out.blanks = np.array(blank_values, float)
    if len(set(out.x)) < 2:
        out.warnings.append("A calibration needs standards at two or more different concentrations "
                            "(set the concentrations in the Responses table).")
        return out
    if len({r.unit for r in std}) > 1:
        out.warnings.append("The responses have different units: " + ", ".join(sorted({r.unit for r in std})) + ".")
    if opts.time is None and len({None if r.time is None else float(r.time) for r in std}) > 1:
        out.warnings.append("Standards measured at different times are pooled: choose a time in the Time setting to calibrate one session.")

    if opts.potentiometric:
        if out.y_unit == "V":                                   # the electrode routines work in mV
            out.y, out.blanks, out.y_unit = out.y * 1000.0, out.blanks * 1000.0, "mV"
            sample_values = [v * 1000.0 for v in sample_values]
        if not _ise_calibration(out, opts):
            return out
        key, all_fits = cal.LOGLIN, out.fits
    else:
        wanted = {"linear": [cal.LINEAR], "loglinear": [cal.LOGLIN], "michaelis": [cal.MICHAELIS], "hill": [cal.HILL]}
        all_fits = cal.fit_all(out.x, out.y)
        out.fits = {k: v for k, v in all_fits.items()}
        key = opts.model if opts.model in wanted else (cal.best_model(all_fits) or cal.LINEAR)
        out.model, out.fit = key, all_fits[key]
        if not out.fit.ok:
            out.warnings.append(f"{cal.MODEL_LABELS[key]}: {out.fit.message}")
            return out
        out.fit_index = np.flatnonzero(out.x > 0) if key == cal.LOGLIN else np.arange(len(out.x))
    out.diagnostics = cal.diagnostics(out.fit, min_residual=pot.TRIM_MV if opts.potentiometric else 0.0)
    for i, why in out.flagged_points():
        out.warnings.append(f"Standard “{out.labels[i]}” ({cal_g(out.x[i])} {out.conc_unit}): {why}. Check the measurement before relying on the fit.")

    if not opts.potentiometric:                                 # an electrode calibration has its figures of merit already
        fom = cal.figures_of_merit(out.x, out.y, blank=out.blanks if out.blanks.size else None, k_lod=opts.k_lod, k_loq=opts.k_loq,
                                   sigma_from=opts.sigma_from, restrict_to_range=opts.restrict_range, r2_min=opts.r2_min,
                                   dev_max=opts.dev_max)
        out.fom = fom
        out.warnings += fom.warnings
        if key == cal.LINEAR:
            out.sensitivity, out.lod, out.loq = fom.sensitivity, fom.lod, fom.loq
            out.sensitivity_unit = f"{out.y_unit}/{out.conc_unit}"
        elif key == cal.LOGLIN:
            out.sensitivity = out.fit.p("slope")
            out.sensitivity_unit = f"{out.y_unit}/decade"
            if out.y_unit in ("mV", "V"):
                theory = cal.nernst_slope(1) / (1000.0 if out.y_unit == "V" else 1.0)
                out.warnings.append(f"Nernstian slope for z = 1 is {theory:.4g} {out.y_unit}/decade (z = 2: {theory / 2:.4g}).")
            out.warnings.append("A log-linear response has no single linear LOD: use the linear-range / intersection method.")
        elif key == cal.MICHAELIS:
            out.sensitivity = out.fit.p("ymax") / out.fit.p("K")
            out.sensitivity_unit = f"{out.y_unit}/{out.conc_unit} (initial slope ymax/K)"
            sigma = fom.sigma
            if math.isfinite(sigma) and out.sensitivity:
                out.lod, out.loq = opts.k_lod * sigma / abs(out.sensitivity), opts.k_loq * sigma / abs(out.sensitivity)
        else:
            out.sensitivity_unit = ""
            out.warnings.append("Hill model: report K (half-saturation) and n; no single sensitivity applies.")

    # quantify samples with the chosen model
    use_fit = out.fit if out.fit.ok else all_fits[cal.LINEAR]
    for r, value in zip(samples, sample_values):
        s = SampleResult(r.source_label or r.id, value, group=r.group)
        s.conc, s.se = cal.predict_concentration(use_fit, value)
        iv = cal.prediction_interval(use_fit, value)
        s.lo, s.hi, s.ci_method = iv.lo, iv.hi, iv.method
        if r.conc is not None:
            try:
                s.expected = conc_to(r.conc, r.conc_unit, out.conc_unit)
            except ValueError:
                pass
        if math.isfinite(s.conc) and s.expected:
            s.recovery_pct = s.conc / s.expected * 100.0
        if not math.isfinite(s.conc):
            out.warnings.append(f"Sample “{s.label}”: the response is outside what the {cal.MODEL_LABELS[key]} model can invert.")
        elif out.ise is not None:
            out.warnings += _ise_sample_remarks(s, out.ise, out.conc_unit)
        out.samples.append(s)
    if out.samples and any(math.isfinite(s.recovery_pct) for s in out.samples):
        out.recovery = studies.recovery([(s.label, s.expected, s.conc) for s in out.samples if math.isfinite(s.recovery_pct)])
    if out.samples and any(r.group != out.samples[0].group for r in std) and not opts.group:
        out.warnings.append("Standards from several electrodes are pooled; select one electrode for a cleaner calibration.")
    return out


# =============================================================================== temperature

@dataclass
class TemperatureRow:
    temp: float
    n: int
    mean: float
    sd: float
    compensated_mean: float


@dataclass
class TemperatureStudy:
    quantity: str
    condition: str
    conc_unit: str
    y_unit: str
    ref_temp: float = 25.0
    conc: Optional[float] = None                 # the concentration studied (None = every standard)
    t: np.ndarray = field(default_factory=lambda: np.array([]))
    y: np.ndarray = field(default_factory=lambda: np.array([]))
    groups: list[str] = field(default_factory=list)
    y_compensated: np.ndarray = field(default_factory=lambda: np.array([]))
    rows: list[TemperatureRow] = field(default_factory=list)
    fit: Optional[cal.ModelFit] = None           # y against (T − T_ref): intercept = the response at the reference temperature
    slope: float = NAN                           # response per °C
    slope_se: float = NAN
    y_ref: float = NAN
    y_ref_se: float = NAN
    alpha: float = NAN                           # temperature coefficient, % of the reference response per °C
    alpha_se: float = NAN
    r2: float = NAN
    rsd_raw: float = NAN                         # RSD of all the responses as measured, %
    rsd_compensated: float = NAN                 # ... and after dividing out the temperature effect
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.fit is not None and self.fit.ok and math.isfinite(self.alpha)


def temperature_compensate(y, temp, alpha_pct_per_c: float, ref_temp: float = 25.0):
    """The responses as they would have been at the reference temperature: y / (1 + α·(T − T_ref))."""
    return np.asarray(y, float) / (1.0 + alpha_pct_per_c / 100.0 * (np.asarray(temp, float) - ref_temp))


def run_temperature(responses: Sequence[Response], quantity: str, condition: str = "analyte", conc: Optional[float] = None,
                    conc_unit: str = "", group: Optional[str] = None, ref_temp: float = 25.0) -> TemperatureStudy:
    """How a sensor's response moves with temperature, and how much compensating for it helps.

    Uses the standards that have a temperature (set it in the Responses table).  The response is fitted as a straight line in
    T − T_ref, so its intercept is the response at the reference temperature and the **temperature coefficient** is slope/intercept,
    in % of that response per °C.  Dividing each response by 1 + α·(T − T_ref) gives what it would have read at T_ref; the RSD
    before and after shows what compensating buys.  A wearable sensor needs this because skin and sweat temperature move by
    several degrees during use.
    """
    rows = [r for r in select(responses, quantity, condition, ("standard",), group) if r.temp_c is not None and math.isfinite(r.temp_c)]
    unit = conc_unit or pick_unit(rows)
    out = TemperatureStudy(quantity=quantity, condition=condition, conc_unit=unit, y_unit=next((r.unit for r in rows), ""), ref_temp=ref_temp)
    if conc is not None:
        out.conc = conc
        try:
            rows = [r for r in rows if r.conc is not None and math.isclose(conc_to(r.conc, r.conc_unit, unit), conc, rel_tol=1e-9, abs_tol=1e-15)]
        except ValueError as exc:
            out.warnings.append(str(exc))
            return out
    elif len({round(conc_to(r.conc, r.conc_unit, unit), 12) for r in rows if r.conc is not None and r.conc_unit in CONC_FACTORS}) > 1:
        out.warnings.append("Responses at several concentrations are pooled: a percentage coefficient only makes sense at one concentration, so choose it.")
    out.t, out.y = np.array([r.temp_c for r in rows], float), np.array([r.value for r in rows], float)
    out.groups = [r.group for r in rows]
    levels = sorted({round(float(v), 2) for v in out.t})
    if len(rows) < 3 or len(levels) < 2:
        out.warnings.append("A temperature study needs responses at two or more temperatures (set the temperature in the Responses table), three points at least.")
        return out
    if len(set(out.groups)) > 1 and not group:
        out.warnings.append("Responses from several electrodes are pooled; select one electrode if their baselines differ.")
    if len(levels) == 2:
        out.warnings.append("Only two temperatures: a line always fits two points, so nothing shows whether the dependence is linear.")
    if levels[-1] - levels[0] < 5.0:
        out.warnings.append(f"The temperature range is only {levels[-1] - levels[0]:.1f} °C: the coefficient is poorly determined.")
    if not levels[0] <= ref_temp <= levels[-1]:
        out.warnings.append(f"The reference temperature {ref_temp:g} °C is outside the range measured ({levels[0]:g}–{levels[-1]:g} °C): the response there is an extrapolation.")

    x = out.t - ref_temp
    fit = cal.fit_linear(x, out.y)
    out.fit = fit
    if not fit.ok:
        out.warnings.append(fit.message)
        return out
    out.slope, out.slope_se, out.y_ref, out.y_ref_se = fit.p("slope"), fit.se("slope"), fit.p("intercept"), fit.se("intercept")
    out.r2 = fit.r2
    if out.y_ref == 0 or abs(out.y_ref) < 1e-12 * max(abs(out.y).max(), 1e-300):
        out.warnings.append("The response at the reference temperature is zero, so a percentage coefficient is undefined.")
        return out
    out.alpha = out.slope / out.y_ref * 100.0
    n = len(x)
    if n > 2:                                                    # delta method for slope/intercept, with their covariance
        s2 = float(np.sum(fit.residuals ** 2) / (n - 2))
        cov = -float(x.mean()) * s2 / float(np.sum((x - x.mean()) ** 2))
        ratio = out.slope / out.y_ref
        var = (out.slope_se ** 2 + ratio ** 2 * out.y_ref_se ** 2 - 2 * ratio * cov) / out.y_ref ** 2
        out.alpha_se = 100.0 * math.sqrt(max(var, 0.0))
        if math.isfinite(out.alpha_se) and abs(out.alpha) < 2 * out.alpha_se:
            out.warnings.append(f"The temperature coefficient ({out.alpha:+.2f} ± {out.alpha_se:.2f} %/°C) is not clearly different from zero.")
    out.y_compensated = temperature_compensate(out.y, out.t, out.alpha, ref_temp)

    def rsd(v: np.ndarray) -> float:
        return float(np.std(v, ddof=1) / abs(np.mean(v)) * 100.0) if len(v) > 1 and np.mean(v) else NAN

    out.rsd_raw, out.rsd_compensated = rsd(out.y), rsd(out.y_compensated)
    for lv in levels:
        m = np.abs(out.t - lv) < 0.005
        out.rows.append(TemperatureRow(lv, int(m.sum()), float(out.y[m].mean()), float(out.y[m].std(ddof=1)) if m.sum() > 1 else NAN,
                                       float(out.y_compensated[m].mean())))
    return out


# =============================================================================== reproducibility

@dataclass
class ReproRow:
    conc: float
    n_groups: int
    n_total: int
    stats: studies.GroupStats
    per_group: dict[str, float]
    outliers: list[str] = field(default_factory=list)


@dataclass
class ReproStudy:
    quantity: str
    condition: str
    conc_unit: str
    y_unit: str
    rows: list[ReproRow] = field(default_factory=list)
    sensitivity: dict[str, float] = field(default_factory=dict)         # per electrode, from its own linear fit
    sensitivity_stats: Optional[studies.GroupStats] = None
    warnings: list[str] = field(default_factory=list)


def run_reproducibility(responses: Sequence[Response], quantity: str, condition: str = "analyte",
                        conc_unit: str = "", time=None) -> ReproStudy:
    std = [r for r in select(responses, quantity, condition, ("standard",), None, time) if r.conc is not None]
    unit = conc_unit or pick_unit(std)
    out = ReproStudy(quantity, condition, unit, next((r.unit for r in std), ""))
    if not std:
        out.warnings.append("No standards with a concentration for this quantity / condition.")
        return out
    by_conc: dict[float, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in std:
        by_conc[round(conc_to(r.conc, r.conc_unit, unit), 12)][r.group or "(no group)"].append(r.value)
    for c in sorted(by_conc):
        per_group = {g_: float(np.mean(v)) for g_, v in by_conc[c].items()}
        vals = list(per_group.values())
        names = list(per_group)
        flagged = [names[i] for i in studies.grubbs_outliers(vals)] if len(vals) >= 3 else []
        out.rows.append(ReproRow(c, len(vals), sum(len(v) for v in by_conc[c].values()), studies.group_stats(vals), per_group, flagged))
    if all(row.n_groups < 2 for row in out.rows):
        out.warnings.append("Reproducibility between electrodes needs the same concentration measured on at least two "
                            "different groups (give each electrode a Group name).")
    by_group: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for r in std:
        by_group[r.group or "(no group)"].append((conc_to(r.conc, r.conc_unit, unit), r.value))
    for g_, pts in by_group.items():
        if len({c for c, _ in pts}) >= 3:
            f = cal.fit_linear([c for c, _ in pts], [v for _, v in pts])
            if f.ok:
                out.sensitivity[g_] = f.p("slope")
    if len(out.sensitivity) >= 2:
        out.sensitivity_stats = studies.group_stats(list(out.sensitivity.values()))
    return out


# =============================================================================== stability

@dataclass
class StabilityStudy:
    quantity: str
    condition: str
    y_unit: str
    conc: Optional[float]
    result: Optional[studies.StabilityResult] = None
    warnings: list[str] = field(default_factory=list)


def run_stability(responses: Sequence[Response], quantity: str, condition: str = "analyte", conc: Optional[float] = None,
                  conc_unit: str = "", group: Optional[str] = None, reference: str = "first") -> StabilityStudy:
    rs = [r for r in select(responses, quantity, condition, ("standard",), group) if r.time is not None]
    unit = conc_unit or pick_unit(rs)
    if conc is not None:
        rs = [r for r in rs if r.conc is not None and math.isclose(conc_to(r.conc, r.conc_unit, unit), conc, rel_tol=1e-6)]
    out = StabilityStudy(quantity, condition, next((r.unit for r in rs), ""), conc)
    if len({r.time for r in rs}) < 2:
        out.warnings.append("A stability study needs the same measurement at two or more times "
                            "(fill the Time column of the Responses table).")
        return out
    by_t: dict[float, list[float]] = defaultdict(list)
    for r in rs:
        by_t[float(r.time)].append(r.value)
    ts = sorted(by_t)
    out.result = studies.stability(ts, [float(np.mean(by_t[t])) for t in ts], reference=reference)
    out.warnings += out.result.warnings
    if conc is None and len({round(conc_to(r.conc, r.conc_unit, unit), 12) for r in rs if r.conc is not None}) > 1:
        out.warnings.append("Several concentrations are averaged together: pick one concentration for a cleaner result.")
    return out


# =============================================================================== selectivity

@dataclass
class SelectivityStudy:
    quantity: str
    analyte: str
    analyte_conc: float = NAN
    analyte_response: float = NAN
    conc_unit: str = ""
    y_unit: str = ""
    rows: list[studies.SelectivityRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def run_selectivity(responses: Sequence[Response], quantity: str, analyte: str = "analyte",
                    analyte_conc: Optional[float] = None, conc_unit: str = "") -> SelectivityStudy:
    std = [r for r in usable(responses) if r.quantity == quantity and r.role == "standard" and r.conc is not None]
    unit = conc_unit or pick_unit(std)
    out = SelectivityStudy(quantity, analyte, conc_unit=unit, y_unit=next((r.unit for r in std), ""))
    ana = [r for r in std if r.condition == analyte]
    others: dict[str, list[Response]] = defaultdict(list)
    for r in std:
        if r.condition != analyte:
            others[r.condition].append(r)
    if not ana or not others:
        out.warnings.append("Selectivity needs responses for the analyte and for at least one other condition "
                            "(set the Condition column, e.g. “ascorbate”).")
        return out
    levels = sorted({round(conc_to(r.conc, r.conc_unit, unit), 12) for r in ana})
    if analyte_conc is None:
        o_conc = [conc_to(r.conc, r.conc_unit, unit) for rs in others.values() for r in rs]
        analyte_conc = min(levels, key=lambda c: abs(c - float(np.median(o_conc))))
    out.analyte_conc = analyte_conc
    vals = [r.value for r in ana if math.isclose(conc_to(r.conc, r.conc_unit, unit), analyte_conc, rel_tol=1e-6)]
    if not vals:
        out.warnings.append("No analyte response at the chosen concentration.")
        return out
    out.analyte_response = float(np.mean(vals))
    items = [(name, float(np.mean([r.value for r in rs])), float(np.mean([conc_to(r.conc, r.conc_unit, unit) for r in rs])))
             for name, rs in sorted(others.items())]
    out.rows = studies.selectivity(out.analyte_response, analyte_conc, items)
    return out
