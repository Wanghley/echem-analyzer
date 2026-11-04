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
from . import studies
from .models import Response

NAN = float("nan")
CONC_FACTORS = {"M": 1.0, "mM": 1e-3, "µM": 1e-6, "uM": 1e-6, "μM": 1e-6, "nM": 1e-9, "pM": 1e-12, "fM": 1e-15}
CONC_UNITS = ["M", "mM", "µM", "nM", "pM", "fM"]
ROLES = ("standard", "blank", "sample")


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


@dataclass
class SampleResult:
    label: str
    response: float
    conc: float = NAN
    se: float = NAN
    expected: float = NAN
    recovery_pct: float = NAN
    group: str = ""


@dataclass
class CalibrationStudy:
    options: CalibrationOptions
    x: np.ndarray = field(default_factory=lambda: np.array([]))
    y: np.ndarray = field(default_factory=lambda: np.array([]))
    groups: list[str] = field(default_factory=list)
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
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.fit is not None and self.fit.ok


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
    out.x, out.y = np.array(xs, float), np.array([r.value for r in std], float)
    out.groups = [r.group for r in std]
    out.blanks = np.array([r.value for r in blanks], float)
    if len(set(out.x)) < 2:
        out.warnings.append("A calibration needs standards at two or more different concentrations "
                            "(set the concentrations in the Responses table).")
        return out
    if len({r.unit for r in std}) > 1:
        out.warnings.append("The responses have different units: " + ", ".join(sorted({r.unit for r in std})) + ".")
    if opts.time is None and len({None if r.time is None else float(r.time) for r in std}) > 1:
        out.warnings.append("Standards measured at different times are pooled: choose a time in the Time setting to calibrate one session.")

    wanted = {"linear": [cal.LINEAR], "loglinear": [cal.LOGLIN], "michaelis": [cal.MICHAELIS], "hill": [cal.HILL]}
    all_fits = cal.fit_all(out.x, out.y)
    out.fits = {k: v for k, v in all_fits.items()}
    key = opts.model if opts.model in wanted else (cal.best_model(all_fits) or cal.LINEAR)
    out.model, out.fit = key, all_fits[key]
    if not out.fit.ok:
        out.warnings.append(f"{cal.MODEL_LABELS[key]}: {out.fit.message}")
        return out

    fom = cal.figures_of_merit(out.x, out.y, blank=out.blanks if out.blanks.size else None, k_lod=opts.k_lod,
                               k_loq=opts.k_loq, sigma_from=opts.sigma_from, restrict_to_range=opts.restrict_range,
                               r2_min=opts.r2_min, dev_max=opts.dev_max)
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
    for r in samples:
        s = SampleResult(r.source_label or r.id, r.value, group=r.group)
        s.conc, s.se = cal.predict_concentration(use_fit, r.value)
        if r.conc is not None:
            try:
                s.expected = conc_to(r.conc, r.conc_unit, out.conc_unit)
            except ValueError:
                pass
        if math.isfinite(s.conc) and s.expected:
            s.recovery_pct = s.conc / s.expected * 100.0
        if not math.isfinite(s.conc):
            out.warnings.append(f"Sample “{s.label}”: the response is outside what the {cal.MODEL_LABELS[key]} model can invert.")
        out.samples.append(s)
    if out.samples and any(math.isfinite(s.recovery_pct) for s in out.samples):
        out.recovery = studies.recovery([(s.label, s.expected, s.conc) for s in out.samples if math.isfinite(s.recovery_pct)])
    if out.samples and any(r.group != out.samples[0].group for r in std) and not opts.group:
        out.warnings.append("Standards from several electrodes are pooled; select one electrode for a cleaner calibration.")
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
