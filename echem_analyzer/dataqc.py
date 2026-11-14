"""Checks on a measurement as it is loaded, before anyone trusts a number from it (no Qt).

Each check looks for something that makes a curve unfit for analysis or worth a second look: points that are not numbers, a
time axis that runs backwards, a current stuck at the instrument's limit, an isolated spike, a potential that has not settled,
units that cannot be right, or the same data loaded twice.  A :class:`Flag` says what was found and how much it matters.

The checks are heuristics with deliberately conservative thresholds (listed below): a flag means "look at this", never "this is
wrong".  They are computed when needed and are not stored in projects.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import numpy as np
from scipy.ndimage import median_filter

from . import techniques
from .models import Curve, DataFile

ERROR, WARNING, INFO = "error", "warning", "info"
_RANK = {INFO: 0, WARNING: 1, ERROR: 2}

MIN_POINTS = 5                     # fewer points than this cannot be analysed
SATURATION_MIN_RUN = 8             # identical extreme values in a row (and at least SATURATION_MIN_FRACTION of the trace)
SATURATION_MIN_FRACTION = 0.02
SPIKE_WINDOW = 7                   # points in the running median a spike is compared with
SPIKE_SIGMAS = 8.0                 # how many robust standard deviations away a point must be ...
SPIKE_MIN_FRACTION = 0.02          # ... and how much of the trace's range, to count as a spike
SPIKE_MIN_POINTS = 15
DRIFT_LIMIT_MV_PER_MIN = 1.0       # an open-circuit potential still moving faster than this has not settled
DRIFT_MIN_SECONDS = 20.0
POTENTIAL_LIMIT_V = 3.0            # a larger potential (window or open circuit) is almost certainly a units mistake
CURRENT_LIMIT_UA = 1e5             # 100 mA


@dataclass(frozen=True)
class Flag:
    code: str                      # nan | short | flat | time-order | clipped | spike | freq | drift | units | duplicate
    severity: str                  # error | warning | info
    message: str


def worst(flags: Sequence[Flag]) -> Optional[str]:
    """The most serious severity among the flags, or None when there are none."""
    return max((f.severity for f in flags), key=_RANK.__getitem__, default=None)


def describe(flags: Sequence[Flag]) -> str:
    """One line per flag, most serious first (tooltips and reports)."""
    ordered = sorted(flags, key=lambda f: -_RANK[f.severity])
    return "\n".join(f.message for f in ordered)


# --------------------------------------------------------------------------- single checks

def _longest_run(mask: np.ndarray) -> int:
    if not mask.any():
        return 0
    edges = np.flatnonzero(np.diff(np.r_[False, mask, False].astype(np.int8)))
    return int((edges[1::2] - edges[0::2]).max())


def _extrapolated(neighbours: np.ndarray, at: float) -> float:
    """Value at position ``at`` of the line through five equally spaced neighbours (positions 0 to 4)."""
    slope, intercept = np.polyfit(np.arange(5.0), neighbours, 1)
    return float(slope * at + intercept)


def _spikes(y: np.ndarray) -> np.ndarray:
    """Indices of isolated points far from their neighbours.  A step is not a spike (the median follows it) and neither is a trend.

    A running median cannot judge the last point: padding the window with the edge value lets a spike outvote itself, and
    padding with a reflection flags any trend.  That point is compared with the line through the five points before it
    instead; it is where PalmSens' end-of-run spike lives.  The first point is not tested: a transient starts steeper than
    any line through what follows it (a charging current, an exponential settling), and that is signal, not a spike.
    """
    if y.size < SPIKE_MIN_POINTS:
        return np.empty(0, int)
    span = float(np.ptp(y))
    if span == 0:
        return np.empty(0, int)
    off = np.abs(y - median_filter(y, size=SPIKE_WINDOW, mode="nearest"))
    off[0] = 0.0
    off[-1] = abs(y[-1] - _extrapolated(y[-6:-1], 5))
    inner = off[1:-1]
    moving = inner[inner > 0]                          # exactly flat stretches (a clipped plateau) say nothing about the noise
    noise = 1.4826 * float(np.median(moving)) if moving.size else 0.0
    threshold = max(SPIKE_SIGMAS * noise, SPIKE_MIN_FRACTION * span)
    return np.flatnonzero(off > threshold)


def _is_time(curve: Curve) -> bool:
    return curve.x_unit.strip().lower() in ("s", "sec")


def _impedance_flags(curve: Curve) -> list[Flag]:
    flags: list[Flag] = []
    freq = curve.extra.get("freq")
    if freq is None or len(freq) == 0:
        return [Flag("freq", WARNING, "No frequency column: the spectrum cannot be fitted.")]
    bad = int((~np.isfinite(freq) | (freq <= 0)).sum())
    if bad:
        flags.append(Flag("freq", ERROR, f"{bad} frequency value(s) are zero, negative or not numbers."))
    arrays = [np.asarray(curve.extra[k], float) for k in ("freq", "zre", "zim") if k in curve.extra]
    missing = int(sum((~np.isfinite(a)).sum() for a in arrays))
    if missing:
        flags.append(Flag("nan", WARNING, f"{missing} impedance value(s) are not numbers."))
    if len(freq) < MIN_POINTS:
        flags.append(Flag("short", WARNING, f"Only {len(freq)} frequencies: too few for an equivalent-circuit fit."))
    return flags


def check_curve(curve: Curve) -> list[Flag]:
    """Everything worth a second look in one curve, most serious first."""
    flags: list[Flag] = []
    x, y = np.asarray(curve.x, float), np.asarray(curve.y, float)
    if curve.kind == techniques.EIS:
        flags += _impedance_flags(curve)
    else:
        bad = int((~np.isfinite(x) | ~np.isfinite(y)).sum())
        if bad:
            share = bad / max(len(x), 1)
            flags.append(Flag("nan", ERROR if share > 0.5 else WARNING,
                              f"{bad} of {len(x)} points are not numbers ({100 * share:.0f} %)."))
        x, y = x[np.isfinite(x) & np.isfinite(y)], y[np.isfinite(x) & np.isfinite(y)]
        if len(x) < MIN_POINTS:
            flags.append(Flag("short", WARNING, f"Only {len(x)} usable point(s): too few to analyse."))
        else:
            flags += _trace_flags(curve, x, y)
    return sorted(flags, key=lambda f: -_RANK[f.severity])


def _trace_flags(curve: Curve, x: np.ndarray, y: np.ndarray) -> list[Flag]:
    flags: list[Flag] = []
    n = len(x)
    if _is_time(curve):
        step = np.diff(x)
        back = int((step <= 0).sum())
        if back:
            first = int(np.flatnonzero(step <= 0)[0]) + 1
            flags.append(Flag("time-order", ERROR, f"Time does not increase at {back} point(s) (first at point {first + 1}): "
                              "rates, step times and drift cannot be trusted."))
    span = float(np.ptp(y))
    potentiometric = curve.kind in (techniques.OCP, techniques.CP) or curve.y_unit == "V"
    if span == 0:
        flags.append(Flag("flat", WARNING, "The signal does not change at all (a flat line): was the cell connected?"))
    else:
        if not potentiometric:
            for target in (float(y.max()), float(y.min())):
                run = _longest_run(y == target)
                if run >= max(SATURATION_MIN_RUN, int(SATURATION_MIN_FRACTION * n)):
                    flags.append(Flag("clipped", WARNING, f"The current stays at exactly {target:.4g} {curve.y_unit} for {run} "
                                      "points: if that is the instrument's limit, the current range was too small and the "
                                      "signal is clipped."))
                    break
        spikes = _spikes(y)
        if spikes.size:
            where = f"{curve.x_unit} = {x[spikes[0]]:.4g}" if curve.x_unit else f"point {spikes[0] + 1}"
            last = " (including the last point, which PalmSens often records wrongly)" if spikes[-1] == n - 1 else ""
            flags.append(Flag("spike", WARNING, f"{spikes.size} isolated spike(s), the first at {where}{last}."))
    flags += _plausibility_flags(curve, x, y)
    if curve.kind == techniques.OCP and curve.y_unit == "V" and _is_time(curve):
        flags += _drift_flags(x, y)
    return flags


def _plausibility_flags(curve: Curve, x: np.ndarray, y: np.ndarray) -> list[Flag]:
    flags: list[Flag] = []
    potential = x if curve.x_unit == "V" else (y if curve.y_unit == "V" else None)
    if potential is not None and np.abs(potential).max() > POTENTIAL_LIMIT_V:
        flags.append(Flag("units", WARNING, f"Potentials up to {np.abs(potential).max():.3g} V: wrong units (mV read as V)?"))
    if curve.y_unit == "µA" and np.abs(y).max() > CURRENT_LIMIT_UA:
        flags.append(Flag("units", WARNING, f"Currents up to {np.abs(y).max() / 1e3:.3g} mA: wrong units (nA read as µA)?"))
    return flags


def _drift_flags(t: np.ndarray, v: np.ndarray) -> list[Flag]:
    if t[-1] - t[0] < DRIFT_MIN_SECONDS:
        return []
    k = max(MIN_POINTS, int(0.2 * len(t)))
    slope = float(np.polyfit(t[-k:], v[-k:], 1)[0]) * 60e3                        # mV per minute
    if abs(slope) > DRIFT_LIMIT_MV_PER_MIN:
        return [Flag("drift", WARNING, f"The potential is still moving by {slope:+.1f} mV/min at the end of the run: "
                     "it has not settled, so the reading depends on when you stop.")]
    return []


# --------------------------------------------------------------------------- across files

def _same_numbers(a: Curve, b: Curve) -> bool:
    """True when two curves hold the same numbers, allowing for a re-export that changed the last digits."""
    if a.kind != b.kind or a.n != b.n:
        return False
    pairs = [(a.x, b.x), (a.y, b.y)] + [(a.extra[k], b.extra[k]) for k in sorted(a.extra) if k in b.extra]
    for u, v in pairs:
        u, v = np.asarray(u, float), np.asarray(v, float)
        if u.shape != v.shape:
            return False
        scale = max(float(np.nanmax(np.abs(u), initial=0.0)), float(np.nanmax(np.abs(v), initial=0.0)), 1e-300)
        if not np.allclose(u, v, rtol=1e-7, atol=1e-9 * scale, equal_nan=True):
            return False
    return True


def check_files(files: Iterable[DataFile]) -> dict[str, list[Flag]]:
    """Flags for every curve, keyed by its uid; a curve whose numbers appeared earlier is flagged as a duplicate."""
    out: dict[str, list[Flag]] = {}
    firsts: dict[tuple[str, int], list[Curve]] = {}               # the first curve of each distinct content, by (kind, length)
    for f in files:
        for c in f.curves:
            flags = check_curve(c)
            if c.n >= MIN_POINTS and np.ptp(np.asarray(c.y, float)) > 0:          # a flat line "matches" every other flat line
                bucket = firsts.setdefault((c.kind, c.n), [])
                original = next((o for o in bucket if _same_numbers(o, c)), None)
                if original is None:
                    bucket.append(c)
                else:
                    flags.append(Flag("duplicate", WARNING, f"Identical to {original.label} ({_file_of(original)}): "
                                      "loaded twice, or the same run saved under two names?"))
            out[c.uid] = sorted(flags, key=lambda fl: -_RANK[fl.severity])
    return out


def _file_of(curve: Curve) -> str:
    return curve.source.replace("\\", "/").rsplit("/", 1)[-1] or "another file"
