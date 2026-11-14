"""Electrode acceptance: pass/fail rules on measured quantities, and the yield of a batch (no Qt).

A rule says what a measured quantity must be for an electrode to go forward: the peak separation of a ferricyanide scan below 80 mV,
the ratio of its peak currents between 0.9 and 1.1, the noise of a blank below a limit.  Rules apply to the responses in the study
table, grouped by electrode, so anything the app can measure can be screened.  The yield of a variant or lot is the share of its tested
electrodes that passed, with a Wilson interval because batches are small and "8 of 10" is not as certain as "80 of 100".

Rules live in the project under ``extras["qc_criteria"]``; a named set can also be saved in the shared preset file to reuse.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from scipy import stats

from .devices import Device
from .models import Response

KEY = "qc_criteria"
PASS, FAIL, MISSING = "pass", "fail", "missing"
OVERALL_PASS, OVERALL_FAIL, OVERALL_INCOMPLETE, OVERALL_NONE = "pass", "fail", "incomplete", "none"
HOW = ("mean", "each", "last")
HOW_LABELS = {"mean": "mean of its measurements", "each": "every measurement", "last": "the latest measurement"}


@dataclass
class Rule:
    quantity: str
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    condition: str = ""                    # "" = responses of any condition
    role: str = ""                         # "" = any role
    how: str = "mean"                      # mean | each | last: what to test when an electrode has several measurements
    name: str = ""                         # a label; made from the bounds when blank

    def label(self) -> str:
        if self.name.strip():
            return self.name.strip()
        lo, hi = self.minimum, self.maximum
        if lo is not None and hi is not None:
            return f"{lo:g} ≤ {self.quantity} ≤ {hi:g}"
        if hi is not None:
            return f"{self.quantity} ≤ {hi:g}"
        if lo is not None:
            return f"{self.quantity} ≥ {lo:g}"
        return f"{self.quantity} (no limit)"

    def to_dict(self) -> dict[str, Any]:
        return {"quantity": self.quantity, "minimum": self.minimum, "maximum": self.maximum, "condition": self.condition,
                "role": self.role, "how": self.how, "name": self.name}

    @classmethod
    def from_dict(cls, d: Any) -> Optional["Rule"]:
        if not isinstance(d, dict) or not str(d.get("quantity", "")).strip():
            return None
        num = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None       # noqa: E731
        lo, hi = num(d.get("minimum")), num(d.get("maximum"))
        if lo is not None and hi is not None and lo > hi:
            lo, hi = hi, lo
        how = d.get("how") if d.get("how") in HOW else "mean"
        return cls(quantity=str(d["quantity"]).strip(), minimum=lo, maximum=hi, condition=str(d.get("condition", "") or ""),
                   role=str(d.get("role", "") or ""), how=how, name=str(d.get("name", "") or ""))


# A starting point for the commonest screen; the quantities are the ones the "Redox couple" analysis of a CV sends to the study.
TEMPLATES: dict[str, list[Rule]] = {
    "Ferri/ferrocyanide screen (CV)": [Rule("ΔEp", maximum=80.0, name="Peak separation ≤ 80 mV"),
                                       Rule("ip ratio", 0.9, 1.1, name="Peak-current ratio 0.9–1.1")],
}


def load(extras: dict) -> list[Rule]:
    return [r for r in (Rule.from_dict(d) for d in extras.get(KEY) or []) if r is not None]


def save(extras: dict, rules: Sequence[Rule]) -> None:
    extras[KEY] = [r.to_dict() for r in rules]


# --------------------------------------------------------------------------- evaluation

@dataclass
class RuleResult:
    rule: Rule
    value: Optional[float]                 # what was tested (the mean, the worst of several, or the latest)
    n: int                                 # how many measurements it came from
    status: str                            # pass | fail | missing
    unit: str = ""


@dataclass
class DeviceResult:
    device: str
    results: list[RuleResult] = field(default_factory=list)
    status: str = OVERALL_NONE             # pass | fail | incomplete (nothing failed, something is unmeasured) | none (no rules)

    def failed(self) -> list[RuleResult]:
        return [r for r in self.results if r.status == FAIL]


def _inside(value: float, rule: Rule) -> bool:
    return (rule.minimum is None or value >= rule.minimum) and (rule.maximum is None or value <= rule.maximum)


def _distance_outside(value: float, rule: Rule) -> float:
    if rule.minimum is not None and value < rule.minimum:
        return rule.minimum - value
    if rule.maximum is not None and value > rule.maximum:
        return value - rule.maximum
    return 0.0


def _test(rule: Rule, rows: list[Response]) -> RuleResult:
    if not rows:
        return RuleResult(rule, None, 0, MISSING)
    unit = rows[0].unit
    values = [float(r.value) for r in rows]
    if rule.how == "each":
        worst = max(values, key=lambda v: _distance_outside(v, rule))
        shown = worst if _distance_outside(worst, rule) > 0 else sum(values) / len(values)
        return RuleResult(rule, shown, len(values), PASS if all(_inside(v, rule) for v in values) else FAIL, unit)
    if rule.how == "last":
        timed = [r for r in rows if r.time is not None]
        last = max(timed, key=lambda r: r.time) if timed else rows[-1]
        return RuleResult(rule, float(last.value), len(values), PASS if _inside(float(last.value), rule) else FAIL, unit)
    mean = sum(values) / len(values)
    return RuleResult(rule, mean, len(values), PASS if _inside(mean, rule) else FAIL, unit)


def evaluate(responses: Iterable[Response], rules: Sequence[Rule], device_ids: Optional[Iterable[str]] = None) -> list[DeviceResult]:
    """Every electrode against every rule.  ``device_ids`` adds electrodes that have no response yet (they come out incomplete)."""
    rows = [r for r in responses if r.use and r.value is not None and math.isfinite(r.value)]
    ids = list(dict.fromkeys([*(r.group for r in rows if r.group), *(device_ids or [])]))
    out = []
    for dev in ids:
        mine = [r for r in rows if r.group == dev]
        results = [_test(rule, [r for r in mine if r.quantity == rule.quantity and (not rule.condition or r.condition == rule.condition)
                                and (not rule.role or r.role == rule.role)]) for rule in rules]
        if not results:
            status = OVERALL_NONE
        elif any(x.status == FAIL for x in results):
            status = OVERALL_FAIL
        elif any(x.status == MISSING for x in results):
            status = OVERALL_INCOMPLETE
        else:
            status = OVERALL_PASS
        out.append(DeviceResult(dev, results, status))
    return out


# --------------------------------------------------------------------------- yield

@dataclass
class YieldRow:
    key: str                               # a variant or a lot; "All devices" for the whole set
    n: int                                 # electrodes in the group
    passed: int
    failed: int
    incomplete: int
    yield_pct: float                       # passed / (passed + failed), among the electrodes that could be judged
    lo_pct: float                          # 95 % Wilson interval of that
    hi_pct: float


def wilson(k: int, n: int, level: float = 0.95) -> tuple[float, float]:
    """The Wilson score interval for k successes in n trials (a proportion, 0–1); (nan, nan) when n is 0."""
    if n <= 0:
        return float("nan"), float("nan")
    z = float(stats.norm.ppf(0.5 + level / 2.0))
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(centre - half, 0.0), min(centre + half, 1.0)


def batch_yield(results: Sequence[DeviceResult], devices: dict[str, Device], by: str = "variant") -> list[YieldRow]:
    """The yield of each variant (``by="variant"``) or lot (``by="lot"``) and of everything together.  Electrodes with no variant or
    lot are grouped as "(not set)"."""
    groups: dict[str, list[DeviceResult]] = {}
    for r in results:
        d = devices.get(r.device)
        key = (getattr(d, by, "") if d is not None else "") or "(not set)"
        groups.setdefault(key, []).append(r)
    rows = []
    for key, items in sorted(groups.items(), key=lambda kv: (kv[0] == "(not set)", kv[0].lower())):
        rows.append(_yield_row(key, items))
    rows.append(_yield_row("All devices", list(results)))
    return rows


def _yield_row(key: str, items: Sequence[DeviceResult]) -> YieldRow:
    passed = sum(i.status == OVERALL_PASS for i in items)
    failed = sum(i.status == OVERALL_FAIL for i in items)
    judged = passed + failed
    lo, hi = wilson(passed, judged)
    return YieldRow(key, len(items), passed, failed, sum(i.status in (OVERALL_INCOMPLETE, OVERALL_NONE) for i in items),
                    passed / judged * 100.0 if judged else float("nan"), lo * 100.0, hi * 100.0)
