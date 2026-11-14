"""Compare design variants of a sensor, and see which fabrication parameter drives performance (no Qt).

Every electrode is calibrated on its own (a straight line through its standards), so a variant is described by how its electrodes
*differ from one another*: the mean sensitivity with a confidence interval from the spread between electrodes, not just one pooled fit.
Alongside come the pooled calibration of the variant (sensitivity, detection limit, linear range), the reproducibility between its
electrodes, the retention over time, the median response time and the share of its electrodes that passed acceptance.  A numeric
fabrication parameter can be set against any per-electrode metric to see whether it matters.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np
from scipy import stats

from . import acceptance as acc
from . import study_analysis as sa
from .devices import Device, is_number, sync
from .models import Response

NAN = float("nan")
NOT_SET = "(not set)"
METRICS = {"sensitivity": "Sensitivity", "lod": "Detection limit"}


@dataclass
class DeviceMetrics:
    device: str
    variant: str
    sensitivity: float = NAN
    lod: float = NAN
    r2: float = NAN
    n_points: int = 0


@dataclass
class VariantRow:
    variant: str
    devices: list[str]
    n_calibrated: int = 0                      # electrodes whose own calibration gave a sensitivity
    sens_mean: float = NAN                     # mean of the electrodes' sensitivities
    sens_sd: float = NAN
    sens_rsd: float = NAN                      # %
    sens_lo: float = NAN                       # 95 % confidence interval of that mean (needs two electrodes)
    sens_hi: float = NAN
    pooled_sensitivity: float = NAN            # all the variant's standards in one calibration
    pooled_lod: float = NAN
    range_low: float = NAN
    range_high: float = NAN
    pooled_r2: float = NAN
    repro_rsd: float = NAN                     # mean RSD between electrodes over the concentration levels, %
    retention: float = NAN                     # % of the first response left at the last time point
    t90: float = NAN                           # median response time, s
    yield_pct: float = NAN
    yield_lo: float = NAN
    yield_hi: float = NAN
    n_judged: int = 0


@dataclass
class Comparison:
    quantity: str
    condition: str
    conc_unit: str = ""
    y_unit: str = ""
    sensitivity_unit: str = ""
    rows: list[VariantRow] = field(default_factory=list)
    per_device: list[DeviceMetrics] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return any(math.isfinite(r.sens_mean) or math.isfinite(r.pooled_sensitivity) for r in self.rows)


def mean_ci(values: Sequence[float], level: float = 0.95) -> tuple[float, float, float, float]:
    """(mean, sd, lo, hi): the mean of a few electrodes with a t interval; the interval is NaN for fewer than two."""
    v = np.asarray([x for x in values if math.isfinite(x)], float)
    if v.size == 0:
        return NAN, NAN, NAN, NAN
    mean = float(v.mean())
    if v.size < 2:
        return mean, NAN, NAN, NAN
    sd = float(v.std(ddof=1))
    half = float(stats.t.ppf(0.5 + level / 2.0, v.size - 1)) * sd / math.sqrt(v.size)
    return mean, sd, mean - half, mean + half


def _linear(responses: Sequence[Response], quantity: str, condition: str, conc_unit: str, group: Optional[str] = None) -> sa.CalibrationStudy:
    return sa.run_calibration(responses, sa.CalibrationOptions(quantity=quantity, condition=condition, group=group, model="linear", conc_unit=conc_unit))


def compare_variants(responses: Sequence[Response], devices: dict[str, Device], quantity: str, condition: str = "analyte",
                     rules: Sequence[acc.Rule] = (), conc_unit: str = "") -> Comparison:
    out = Comparison(quantity=quantity, condition=condition)
    devs = sync(devices, responses)
    by_variant: dict[str, list[str]] = {}
    for d in devs.values():
        by_variant.setdefault(d.variant or NOT_SET, []).append(d.id)
    verdicts = {r.device: r for r in acc.evaluate(responses, rules, devs.keys())} if rules else {}
    yields = {y.key: y for y in acc.batch_yield(list(verdicts.values()), devs, "variant")} if verdicts else {}

    for variant, ids in sorted(by_variant.items(), key=lambda kv: (kv[0] == NOT_SET, kv[0].lower())):
        sub = [r for r in responses if r.group in ids]
        row = VariantRow(variant=variant, devices=ids)
        sens = []
        for dev in ids:
            cs = _linear(sub, quantity, condition, conc_unit, dev)
            dm = DeviceMetrics(dev, variant)
            if cs.ok:
                dm.sensitivity, dm.lod, dm.r2, dm.n_points = cs.sensitivity, cs.lod, cs.fit.r2, len(cs.x)
                out.conc_unit, out.y_unit, out.sensitivity_unit = cs.conc_unit, cs.y_unit, cs.sensitivity_unit
                sens.append(cs.sensitivity)
            out.per_device.append(dm)
        row.n_calibrated = len(sens)
        row.sens_mean, row.sens_sd, row.sens_lo, row.sens_hi = mean_ci(sens)
        if len(sens) > 1 and row.sens_mean:
            row.sens_rsd = abs(row.sens_sd / row.sens_mean) * 100.0
        pooled = _linear(sub, quantity, condition, conc_unit)
        if pooled.ok:
            row.pooled_sensitivity, row.pooled_lod, row.pooled_r2 = pooled.sensitivity, pooled.lod, pooled.fit.r2
            if pooled.fom is not None and pooled.fom.n_range:
                row.range_low, row.range_high = pooled.fom.range_low, pooled.fom.range_high
        rep = sa.run_reproducibility(sub, quantity, condition, conc_unit)
        rsds = [x.stats.rsd_pct for x in rep.rows if x.n_groups >= 2 and math.isfinite(x.stats.rsd_pct)]
        row.repro_rsd = float(np.mean(rsds)) if rsds else NAN
        stab = sa.run_stability(sub, quantity, condition, None, "", None, "first")
        if stab.result is not None:
            row.retention = stab.result.final_retention_pct
        t90 = [r.value for r in sub if r.quantity == "t90" and r.use and math.isfinite(r.value)]
        row.t90 = float(np.median(t90)) if t90 else NAN
        y = yields.get(variant)
        if y is not None:
            row.yield_pct, row.yield_lo, row.yield_hi, row.n_judged = y.yield_pct, y.lo_pct, y.hi_pct, y.passed + y.failed
        out.rows.append(row)

    if not out.ok:
        out.warnings.append("No electrode has two or more standards at different concentrations: set the Group and the concentration in the Responses table.")
    if len(out.rows) < 2:
        out.warnings.append("Only one variant: give the electrodes a variant in the Devices tab to compare them.")
    if any(r.n_calibrated == 1 for r in out.rows):
        out.warnings.append("A variant with one calibrated electrode has no between-electrode spread: its interval is empty.")
    return out


# --------------------------------------------------------------------------- what drives performance

@dataclass
class Trend:
    parameter: str
    metric: str
    x: np.ndarray
    y: np.ndarray
    ids: list[str]
    slope: float = NAN
    intercept: float = NAN
    r: float = NAN
    p: float = NAN

    @property
    def ok(self) -> bool:
        return math.isfinite(self.slope)


def trend(comparison: Comparison, devices: dict[str, Device], parameter: str, metric: str = "sensitivity") -> Trend:
    """A fabrication parameter against a per-electrode metric (``sensitivity`` or ``lod``), with a least-squares line.

    Needs three electrodes with a number for the parameter and a value for the metric, at two or more different parameter values.
    A p-value from three or four points shows how little they can say, so it is reported, not hidden.
    """
    xs, ys, ids = [], [], []
    for dm in comparison.per_device:
        d = devices.get(dm.device)
        value = getattr(dm, metric, NAN)
        if d is not None and is_number(d.params.get(parameter)) and math.isfinite(value):
            xs.append(float(d.params[parameter]))
            ys.append(float(value))
            ids.append(dm.device)
    t = Trend(parameter, metric, np.array(xs), np.array(ys), ids)
    if len(xs) >= 3 and len(set(xs)) >= 2:
        fit = stats.linregress(xs, ys)
        t.slope, t.intercept, t.r, t.p = float(fit.slope), float(fit.intercept), float(fit.rvalue), float(fit.pvalue)
    return t
