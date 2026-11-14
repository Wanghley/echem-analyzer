"""Response and recovery times, drift correction and temperature: what a sensor worn on skin needs."""
import math

import numpy as np
import pytest

from echem_analyzer import analyzers, transients
from echem_analyzer.models import Curve

TAU = 2.0


def first_order(t, t_step, y0, y1, tau=TAU):
    return np.where(t < t_step, y0, y0 + (y1 - y0) * (1 - np.exp(-(t - t_step) / tau)))


# ===================================================================== T95 and recovery

def test_t95_of_a_first_order_response_is_three_time_constants():
    t = np.arange(0, 60, 0.01)
    r = transients.response_time(t, first_order(t, 10.0, 1.0, 5.0), t_step=10.0, pre=5.0, post_window=10.0, t_end=59.0)
    assert r.t_95 == pytest.approx(-TAU * math.log(0.05), rel=0.03)                   # 2.996 τ
    assert r.t_95 > r.t_high > r.t_low > 0
    assert r.t_high == pytest.approx(-TAU * math.log(0.1), rel=0.03)


def test_the_time_to_recover_is_the_same_measurement_on_the_falling_step():
    t = np.arange(0, 60, 0.01)
    rise = transients.response_time(t, first_order(t, 10.0, 1.0, 5.0), 10.0, pre=5.0, post_window=10.0, t_end=59.0)
    fall = transients.response_time(t, first_order(t, 10.0, 5.0, 1.0), 10.0, pre=5.0, post_window=10.0, t_end=59.0)
    assert fall.t_high == pytest.approx(rise.t_high, rel=0.02) and fall.t_95 == pytest.approx(rise.t_95, rel=0.02)
    assert fall.y_start > fall.y_end                                                    # it really did fall


def test_one_noisy_sample_is_not_mistaken_for_the_response_arriving():
    t = np.arange(0, 60, 0.05)
    y = first_order(t, 10.0, 1.0, 5.0) + np.random.default_rng(2).normal(0, 0.02, t.size)
    clean = transients.response_time(t, y, 10.0, pre=5.0, post_window=10.0, t_end=59.0)
    k = int(np.searchsorted(t, 10.5))
    y[k] = 5.2                                                                          # a glitch to the final level half a second after the step
    glitched = transients.response_time(t, y, 10.0, pre=5.0, post_window=10.0, t_end=59.0)
    assert glitched.t_95 == pytest.approx(clean.t_95, abs=0.3) and glitched.t_95 > 4.0
    assert glitched.t_high == pytest.approx(clean.t_high, abs=0.3)


# ===================================================================== the staircase analyzer

def up_and_down(tau=0.8, seed=4):
    """Baseline 0 -> +1 -> +2 (the analyte is added) -> +1 -> 0 (it is washed out), each change a first-order response."""
    t = np.arange(0, 120, 0.05)
    y = np.zeros_like(t)
    for ts, change in ((20, 1.0), (45, 1.0), (70, -1.0), (95, -1.0)):
        m = t >= ts
        y[m] += change * (1 - np.exp(-(t[m] - ts) / tau))
    return t, y + np.random.default_rng(seed).normal(0, 0.003, t.size)


def staircase(t, y, **extra):
    c = Curve(kind="CA", title="additions", x=t, y=y, x_unit="s", y_unit="µA", uid="u")
    a = analyzers.ANALYZERS["staircase"]
    return a.run(c, a.params_for({"drop_last": 0, "window": 5.0, "sharp": "slow", **extra}))


def test_by_default_a_jump_the_other_way_is_not_an_addition_and_is_only_reported():
    t, y = up_and_down()
    r = staircase(t, y)
    assert r.ok and r.metrics[0].value == "2"                                             # the two washes are left out
    assert not any(s.quantity.startswith("Recovery") for s in r.responses)
    assert any("opposite" in w.lower() or "not additions" in w.lower() or "jump" in w.lower() for w in r.warnings)


def test_steps_against_the_first_one_are_recoveries_with_their_own_times():
    t, y = up_and_down(tau=0.8)
    r = staircase(t, y, opposite="recovery")
    assert r.ok and r.metrics[0].value == "4"
    header, rows = r.table
    assert header[-2:] == ["t90 (s)", "t95 (s)"]
    assert [row[0] for row in rows] == ["0 (baseline)", "1", "2", "3 (recovery)", "4 (recovery)"]
    by_quantity = {}
    for s in r.responses:
        by_quantity.setdefault(s.quantity, []).append(s.value)
    assert set(by_quantity) >= {"t90", "t95", "Recovery t90", "Recovery t95", "Plateau", "Step change (Δ)"}
    assert len(by_quantity["t90"]) == len(by_quantity["Recovery t90"]) == 2
    assert by_quantity["t90"] == pytest.approx([-0.8 * math.log(0.1)] * 2, rel=0.3)
    assert by_quantity["t95"] == pytest.approx([-0.8 * math.log(0.05)] * 2, rel=0.3)
    assert by_quantity["Recovery t90"] == pytest.approx(by_quantity["t90"], rel=0.3)
    assert by_quantity["Recovery t95"] == pytest.approx(by_quantity["t95"], rel=0.3)
    assert all(a < b for a, b in zip(by_quantity["t90"], by_quantity["t95"]))


def test_a_staircase_of_additions_alone_has_no_recoveries():
    from test_study import slow_staircase
    t, y = slow_staircase(0.8)
    r = staircase(t, y)
    assert not any(s.quantity.startswith("Recovery") for s in r.responses)
    assert all(not row[0].endswith("(recovery)") for row in r.table[1])
    assert sum(s.quantity == "t95" for s in r.responses) == 3


# ===================================================================== baseline drift

def drifting(rate_per_min=0.6, tau=0.8, seed=6):
    """The additions staircase of test_study (true changes -0.5, -0.9 and -1.2 against the baseline) on a baseline that drifts."""
    from test_study import slow_staircase
    t, y = slow_staircase(tau, seed)
    return t, y + rate_per_min / 60.0 * t


def deltas(result):
    return [s.value for s in result.responses if s.quantity == "Step change (Δ)"]


def test_a_straight_line_drift_is_removed_so_the_plateaus_show_what_the_analyte_did():
    t, y = drifting(0.6)                                                              # +0.6 µA/min: 1 µA over the run, as big as the signal
    raw = staircase(t, y)
    fixed = staircase(t, y, detrend="linear")
    assert deltas(raw) != pytest.approx([-0.5, -0.9, -1.2], abs=0.1)                  # the drift is in the steps
    assert deltas(fixed) == pytest.approx([-0.5, -0.9, -1.2], abs=0.04)
    plateaus = [s.value for s in fixed.responses if s.quantity == "Plateau"]
    assert plateaus == pytest.approx([-0.3, -0.8, -1.2, -1.5], abs=0.05)
    row = next(r for r in fixed.rows if r[0] == "Baseline drift removed")
    assert float(row[1]) == pytest.approx(0.6, rel=0.1) and row[2] == "µA/min"


def test_the_corrected_trace_is_what_is_drawn_and_the_original_is_untouched():
    t, y = drifting(0.6)
    c = Curve(kind="CA", title="additions", x=t, y=y.copy(), x_unit="s", y_unit="µA", uid="u")
    a = analyzers.ANALYZERS["staircase"]
    r = a.run(c, a.params_for({"drop_last": 0, "window": 5.0, "sharp": "slow", "detrend": "linear"}))
    shown = r.data[0]
    assert np.array_equal(c.y, y) and not np.array_equal(shown.y, y) and len(shown.y) == len(y)
    assert shown.y[0] == pytest.approx(y[0])                                          # anchored at the start of the trace
    assert shown.y[-1] - y[-1] == pytest.approx(-0.6 / 60 * (t[-1] - t[0]), rel=0.03)   # the drift estimate is good to a few percent
    none = a.run(c, a.params_for({"drop_last": 0, "window": 5.0, "sharp": "slow"}))
    assert none.data[0] is c


def test_too_little_baseline_leaves_the_trace_alone_and_says_why():
    t, y = drifting(0.6)
    keep = t >= 18.0                                                                  # only 7 s before the first step at 25 s
    c = Curve(kind="CA", title="short", x=t[keep], y=y[keep], x_unit="s", y_unit="µA", uid="u")
    a = analyzers.ANALYZERS["staircase"]
    r = a.run(c, a.params_for({"drop_last": 0, "window": 5.0, "sharp": "slow", "detrend": "linear"}))
    assert r.ok and any("needs at least 10 s" in w for w in r.warnings)
    assert not any(row[0] == "Baseline drift removed" for row in r.rows) and r.data[0] is c


def test_a_drift_free_trace_is_not_changed_by_asking_for_the_correction():
    from test_study import slow_staircase
    t, y = slow_staircase(0.8)
    plain, corrected = staircase(t, y), staircase(t, y, detrend="linear")
    assert deltas(corrected) == pytest.approx(deltas(plain), abs=0.02)
    drift = float(next(r for r in corrected.rows if r[0] == "Baseline drift removed")[1])
    assert abs(drift) < 0.05                                                          # µA/min: nothing to remove
