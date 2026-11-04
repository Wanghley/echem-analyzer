import math

import numpy as np
import pytest

from echem_analyzer.ca_analysis import (
    added_concentrations,
    analyze_ca,
    detect_step_events,
    detect_steps,
    linear_fit,
    sync_step_lists,
)
from echem_analyzer.io_palmsens import load_csv, load_pssession
from echem_analyzer.models import KIND_CA, CAAnalysisSettings


# ------------------------------------------------------------------ synthetic data

def synthetic_staircase(
    slope=-0.05, baseline=-0.3, n_steps=5, spacing=20.0, first=20.0, drift=0.0,
    noise=0.0005, dt=0.1, seed=1,
):
    """Cathodic staircase with exact levels ``baseline + slope*k`` (k = number of additions).

    Each step has a short settling undershoot (tau = 0.7 s) and the whole trace carries a
    continuous linear baseline drift of ``drift`` µA/s, plus white noise.
    """
    rng = np.random.default_rng(seed)
    steps = [first + k * spacing for k in range(n_steps)]
    t = np.arange(0, steps[-1] + spacing, dt)
    i = np.full_like(t, baseline)
    for k, ts in enumerate(steps):
        seg = t >= ts
        i[seg] = baseline + slope * (k + 1)
        i[seg] += 0.05 * np.exp(-(t[seg] - ts) / 0.7) * np.sign(slope)
    i += drift * t
    i += rng.normal(0, noise, size=t.shape)
    return t, i, steps


def _settings(steps, method="plateau", **kw):
    return CAAnalysisSettings(
        method=method, step_times=list(steps), stock_conc=10.0, stock_unit="mM",
        v0_ml=10.0, v_add_ul=100.0, drop_last=0, **kw,
    )


def _exact_fit():
    """Fit of the exact (noise-free, drift-free) levels the generator encodes."""
    conc = added_concentrations(10.0, 10.0, [100] * 5)
    return linear_fit(conc, 0.3 + 0.05 * np.arange(6))


# ------------------------------------------------------------------ pure maths

def test_linear_fit_known():
    x = np.array([0, 1, 2, 3, 4], float)
    y = 2.0 * x + 1.0
    f = linear_fit(x, y)
    assert f.slope == pytest.approx(2.0)
    assert f.intercept == pytest.approx(1.0)
    assert f.r2 == pytest.approx(1.0)
    assert f.x_intercept == pytest.approx(-0.5)


def test_linear_fit_uncertainty_hand_computed():
    # y = [1.1, 2.9, 5.2, 6.8], x = [0, 1, 2, 3]  -> verified by hand
    x = np.array([0, 1, 2, 3.0])
    y = np.array([1.1, 2.9, 5.2, 6.8])
    f = linear_fit(x, y)
    sxx = 5.0
    slope = ((x - 1.5) * (y - y.mean())).sum() / sxx
    assert f.slope == pytest.approx(slope)
    s_yx = math.sqrt(((y - (slope * x + (y.mean() - slope * 1.5))) ** 2).sum() / 2)
    assert f.s_yx == pytest.approx(s_yx)
    assert f.slope_se == pytest.approx(s_yx / math.sqrt(sxx))
    cx_se = (s_yx / slope) * math.sqrt(1 / 4 + y.mean() ** 2 / (slope**2 * sxx))
    assert f.x_intercept_se == pytest.approx(cx_se)


def test_dilution_formula_worked_example():
    # 505.1 mM stock, 10 mL cell, 20 µL per addition
    c = added_concentrations(505.1, 10.0, [20, 20, 20], dilution=True)
    assert c[0] == 0
    assert c[1] == pytest.approx(505.1 * 20 / 10020)
    assert c[3] == pytest.approx(505.1 * 60 / 10060)
    c2 = added_concentrations(505.1, 10.0, [20, 20, 20], dilution=False)
    assert c2[3] == pytest.approx(505.1 * 60 / 10000)
    c3 = added_concentrations(505.1, 10.0, [20, 20], overrides=[None, 1.5])
    assert c3[2] == 1.5


# ------------------------------------------------------------------ synthetic pipeline

@pytest.mark.parametrize("method", ["plateau", "drift"])
def test_recovers_slope_and_intercept_without_drift(method):
    """5 additions of 100 µL of a 10 mM stock into 10 mL (dilution-corrected)."""
    t, i, steps = synthetic_staircase()
    r = analyze_ca(t, i, _settings(steps, method))
    exact = _exact_fit()
    assert r.polarity == -1.0
    assert r.fit.r2 > 0.999
    assert r.fit.slope == pytest.approx(exact.slope, rel=0.02)
    assert r.c_x == pytest.approx(exact.intercept / exact.slope, rel=0.02)
    assert r.sensitivity == pytest.approx(exact.slope, rel=0.02)


def test_drift_method_removes_baseline_drift():
    """A continuous drift biases the end-of-segment plateau method by ~drift*spacing per
    step; the drift-corrected method evaluates pre/post lines at the step and is immune."""
    t, i, steps = synthetic_staircase(drift=0.001)
    exact = _exact_fit()
    plateau = analyze_ca(t, i, _settings(steps, "plateau"))
    drift = analyze_ca(t, i, _settings(steps, "drift"))
    err_plateau = abs(plateau.fit.slope / exact.slope - 1)
    err_drift = abs(drift.fit.slope / exact.slope - 1)
    assert err_drift < 0.02
    assert err_plateau > 0.10          # 0.001 µA/s * 20 s = 0.02 µA on a 0.05 µA step (40%)
    assert err_plateau > 5 * err_drift


def test_lod_loq_formula():
    t, i, steps = synthetic_staircase(noise=0.002, drift=0.0)
    s = CAAnalysisSettings(step_times=list(steps), stock_conc=10.0, v0_ml=10.0, v_add_ul=100.0, drop_last=0, sigma_source="baseline")
    r = analyze_ca(t, i, s)
    assert r.sigma == pytest.approx(0.002, rel=0.4)                                   # the noise of the trace
    assert r.lod == pytest.approx(3.3 * r.sigma / r.sensitivity)                       # LOD = 3.3σ/S, LOQ = 10σ/S by default
    assert r.loq == pytest.approx(10 * r.sigma / r.sensitivity)
    s.sigma_source, s.lod_k = "fit", 3.0                                               # the default is s(y/x); k is adjustable
    r = analyze_ca(t, i, s)
    assert r.sigma == pytest.approx(r.fit.s_yx) and r.lod == pytest.approx(3.0 * r.fit.s_yx / r.sensitivity)


def test_c_sample_dilution_of_sample():
    t, i, steps = synthetic_staircase()
    s = CAAnalysisSettings(
        step_times=list(steps), stock_conc=10.0, v0_ml=10.0, v_add_ul=100.0,
        v_sample_ml=2.0, drop_last=0,
    )
    r = analyze_ca(t, i, s)
    assert r.c_sample == pytest.approx(r.c_x * 5.0)


def test_excluding_points_changes_fit():
    t, i, steps = synthetic_staircase()
    s = CAAnalysisSettings(step_times=list(steps), stock_conc=10.0, v0_ml=10.0, v_add_ul=100.0, drop_last=0)
    full = analyze_ca(t, i, s)
    s.step_included[-1] = False
    part = analyze_ca(t, i, s)
    assert part.fit.n == full.fit.n - 1


def test_no_steps_is_safe():
    t = np.arange(0, 30, 0.1)
    i = np.full_like(t, -0.5)
    s = CAAnalysisSettings(drop_last=0)
    r = analyze_ca(t, i, s)
    assert len(r.step_times) == 0 and r.fit is None


def test_sync_step_lists_pads():
    s = CAAnalysisSettings(step_times=[1.0, 2.0, 3.0], v_add_ul=25.0)
    sync_step_lists(s)
    assert s.step_volumes_ul == [25.0] * 3
    assert s.step_included == [True] * 3
    assert s.step_conc_override == [None] * 3


# ------------------------------------------------------------------ detection on synthetic data

def test_detect_steps_synthetic():
    t, i, steps = synthetic_staircase()
    found = detect_steps(t, i)
    assert len(found) == 5
    assert np.allclose(found, steps, atol=0.3)


def test_detect_ignores_opposite_spike():
    t, i, steps = synthetic_staircase()
    i = i.copy()
    i[-1] += 5.0                       # end-of-run spike, opposite sign
    found = detect_steps(t, i)
    assert len(found) == 5


def test_expected_count():
    t, i, steps = synthetic_staircase()
    assert len(detect_steps(t, i, expected=3)) == 3


def test_smooth_decay_is_not_a_step():
    """A conditioning-style exponential decay has no abrupt jump -> no steps."""
    t = np.arange(0, 200, 0.1)
    i = -0.25 - 1.3 * np.exp(-t / 8.0)
    steps, ignored = detect_step_events(t, i)
    assert len(steps) == 0 and len(ignored) == 0


def test_opposite_direction_jump_is_reported_not_silently_dropped():
    t, i, steps = synthetic_staircase()
    i = i.copy()
    i[t >= 30.0] += 0.2               # upward jump between additions 1 and 2 (cathodic staircase)
    found, ignored = detect_step_events(t, i)
    assert len(found) == 5
    assert len(ignored) == 1 and ignored[0] == pytest.approx(30.0, abs=0.3)
    s = _settings(steps)
    s.ignored_events = [float(x) for x in ignored]
    r = analyze_ca(t, i, s)
    assert any("opposite direction" in w for w in r.warnings)


# ------------------------------------------------------------------ real data

B2_STEPS = [20.0, 41.7, 65.3, 88.0, 110.9]
B5_STEPS = [26.6, 53.2, 83.5, 115.8, 146.5]


def _ca(path):
    return load_csv(path).of_kind(KIND_CA)[0]


def test_detect_b2(data_dir):
    c = _ca(data_dir / "2026-11-02 B2 GY data.csv")
    found = detect_steps(c.x[:-1], c.y[:-1])
    assert len(found) == 5
    assert np.allclose(found, B2_STEPS, atol=0.5)


def test_detect_b5(data_dir):
    c = _ca(data_dir / "2026-11-02 B5 CSF C1 data.csv")
    found = detect_steps(c.x[:-1], c.y[:-1])
    assert len(found) == 5
    assert np.allclose(found, B5_STEPS, atol=0.5)


@pytest.mark.parametrize("name", ["2026-11-02 B2 GY data.csv", "2026-11-02 B5 CSF C1 data.csv"])
def test_real_pipeline_runs_and_is_monotonic(data_dir, name):
    c = _ca(data_dir / name)
    found = detect_steps(c.x[:-1], c.y[:-1])
    s = CAAnalysisSettings(
        step_times=[float(x) for x in found], stock_conc=505.1, v0_ml=10.0, v_add_ul=10.0,
    )
    r = analyze_ca(c.x, c.y, s)
    assert r.polarity == -1.0
    assert np.all(np.diff(r.signal) > 0)          # each addition raises |I|
    assert r.fit is not None and r.fit.r2 > 0.9
    assert r.sensitivity > 0 and math.isfinite(r.lod)


def test_a6_pssession_runs(data_dir):
    f = load_pssession(data_dir / "2026-11-02 A6 GY data.pssession")
    ca = [c for c in f.of_kind(KIND_CA) if c.meta["run_title"].startswith("std addition")][0]
    found = detect_steps(ca.x[:-1], ca.y[:-1])
    assert 2 <= len(found) <= 12


def test_a6_calibration_decay_has_no_steps(data_dir):
    f = load_pssession(data_dir / "2026-11-02 A6 GY data.pssession")
    cal = next(c for c in f.of_kind(KIND_CA) if c.meta["run_title"] == "calibration")
    steps, ignored = detect_step_events(cal.x[:-1], cal.y[:-1])
    assert len(steps) == 0


def test_a6_std_addition_flags_the_upward_event(data_dir):
    """A6's run has a +0.23 µA jump at ~36 s between two additions; it must be reported."""
    f = load_pssession(data_dir / "2026-11-02 A6 GY data.pssession")
    run = next(c for c in f.of_kind(KIND_CA) if c.meta["run_title"].startswith("std addition"))
    steps, ignored = detect_step_events(run.x[:-1], run.y[:-1])
    assert np.allclose(steps, [12.2, 22.9, 49.7, 65.4], atol=0.5)
    assert len(ignored) == 1 and ignored[0] == pytest.approx(36.0, abs=0.5)
    s = CAAnalysisSettings(step_times=[float(x) for x in steps], ignored_events=[float(x) for x in ignored],
                           stock_conc=505.1, v0_ml=10.0, v_add_ul=10.0)
    assert any("opposite direction" in w for w in analyze_ca(run.x, run.y, s).warnings)
