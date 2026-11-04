import math

import numpy as np
import pytest

from echem_analyzer.cv_analysis import (
    analyze_cycle,
    analyze_run,
    group_cv_runs,
    stability,
    summarize_stability,
    sweep_masks,
    window_baselines,
)
from echem_analyzer.io_palmsens import load_csv, load_pssession
from echem_analyzer.models import KIND_CV, CVSettings


# ------------------------------------------------------------------ synthetic waveforms

def triangle(e_lo=-0.3, e_hi=0.3, step=0.005, start_low=True):
    up = np.arange(e_lo, e_hi, step)
    down = np.arange(e_hi, e_lo, -step)
    e = np.r_[up, down]
    return e if start_low else np.r_[down, up]


def gaussian(e, e0, width, height):
    return height * np.exp(-0.5 * ((e - e0) / width) ** 2)


# ------------------------------------------------------------------ branches

def test_sweep_masks_split_at_the_vertex():
    e = triangle()
    up, down = sweep_masks(e)
    assert up.sum() + down.sum() == len(e)
    assert np.all(np.diff(e)[up[:-1]] > 0) and np.all(np.diff(e)[down[:-1]] < 0)
    assert e[up].max() < e.max() + 1e-12 and e[np.argmax(e)] == e.max()


def test_sweep_masks_work_when_the_scan_starts_at_the_top():
    e = triangle(start_low=False)
    up, down = sweep_masks(e)
    assert down[0] and not up[0]                          # first sweep is negative-going
    assert up.sum() > 0 and down.sum() > 0


# ------------------------------------------------------------------ peaks

def test_peaks_found_on_the_right_branch():
    e = triangle()
    i = gaussian(e, 0.15, 0.03, 10.0) * (np.diff(e, append=e[-1] + 1) > 0) \
        - gaussian(e, -0.05, 0.03, 8.0) * (np.diff(e, append=e[-1] - 1) < 0)
    r = analyze_cycle(e, i, CVSettings(), cycle=3)
    assert r.cycle == 3
    assert r.e_pa == pytest.approx(0.15, abs=0.006) and r.i_pa == pytest.approx(10.0, rel=0.02)
    assert r.e_pc == pytest.approx(-0.05, abs=0.006) and r.i_pc == pytest.approx(-8.0, rel=0.02)
    assert r.delta_ep == pytest.approx(0.20, abs=0.012)
    assert r.e_half == pytest.approx(0.05, abs=0.006)
    assert r.ip_ratio == pytest.approx(10.0 / 8.0, rel=0.03)


def test_window_excludes_a_larger_unwanted_feature():
    """A big spike outside the anodic window must not be reported as the peak."""
    e = triangle()
    up = np.diff(e, append=e[-1] + 1) > 0
    i = np.where(up, gaussian(e, 0.15, 0.02, 5.0) + gaussian(e, -0.28, 0.01, 50.0), 0.0)
    assert analyze_cycle(e, i, CVSettings()).e_pa == pytest.approx(-0.28, abs=0.006)
    r = analyze_cycle(e, i, CVSettings(anodic_window=[0.0, 0.3]))
    assert r.e_pa == pytest.approx(0.15, abs=0.006) and r.i_pa == pytest.approx(5.0, rel=0.02)


def test_linear_baseline_removes_a_sloping_background():
    e = triangle()
    up = np.diff(e, append=e[-1] + 1) > 0
    background = 20.0 * (e + 0.3)                                   # 0 -> 12 µA ramp
    i = np.where(up, background + gaussian(e, 0.0, 0.03, 4.0), 0.0)
    raw = analyze_cycle(e, i, CVSettings())
    corr = analyze_cycle(e, i, CVSettings(anodic_window=[-0.2, 0.2], baseline="linear"))
    assert raw.e_pa > 0.25                                           # raw max rides the ramp to the top
    assert corr.e_pa == pytest.approx(0.0, abs=0.02)
    assert corr.i_pa == pytest.approx(4.0, rel=0.1)


def test_empty_window_gives_nan_not_an_error():
    e = triangle()
    r = analyze_cycle(e, np.zeros_like(e), CVSettings(anodic_window=[5.0, 6.0]))
    assert math.isnan(r.e_pa) and math.isnan(r.i_pa)


# ------------------------------------------------------------------ charge / capacitance

def test_capacitance_of_a_purely_capacitive_cycle():
    """i = +C·ν on the way up, -C·ν on the way down  ->  C is recovered exactly."""
    nu, c_uF = 0.05, 40.0                       # V/s, µF  -> i = 2 µA
    e = triangle()
    up, _ = sweep_masks(e)
    i = np.where(up, c_uF * nu, -c_uF * nu)
    r = analyze_cycle(e, i, CVSettings(scan_rate=nu))
    assert r.capacitance == pytest.approx(c_uF, rel=0.03)
    assert r.q_anodic == pytest.approx(c_uF * 0.6, rel=0.03) and r.q_cathodic == pytest.approx(-c_uF * 0.6, rel=0.03)


def test_no_scan_rate_means_no_charge():
    e = triangle()
    r = analyze_cycle(e, np.ones_like(e), CVSettings(scan_rate=0.0))
    assert math.isnan(r.q_anodic) and math.isnan(r.capacitance)


# ------------------------------------------------------------------ stability across cycles

def test_stability_detects_when_the_cycles_settle():
    s = stability([1, 2, 3, 4, 5], [8.9, 10.3, 10.97, 10.99, 11.00], tol_pct=1.0)
    assert s.rel_change_pct[0] != s.rel_change_pct[0]                 # first is NaN
    assert s.rel_change_pct[1] == pytest.approx(100 * 1.4 / 8.9)
    assert s.stable_from == 3
    assert s.total_drift_pct == pytest.approx(100 * (11.0 - 8.9) / 8.9)


def test_stability_is_none_while_still_changing():
    s = stability([1, 2, 3, 4, 5], [8.93, 10.33, 10.97, 11.36, 11.63], tol_pct=1.0)
    assert s.stable_from is None
    assert s.last_change_pct == pytest.approx(100 * (11.63 - 11.36) / 11.36)


def test_stability_with_one_cycle_is_safe():
    s = stability([1], [5.0], 1.0)
    assert s.stable_from is None and math.isnan(s.total_drift_pct)


def test_excluded_cycles_are_left_out():
    e = triangle()
    from echem_analyzer.models import Curve
    run = group_cv_runs([
        Curve(kind=KIND_CV, title=f"CV i vs E Scan {n}", x=e, y=np.sin(e) * n, scan=n,
              meta={"run_title": "Cyclic Voltammetry"}, source="f.csv")
        for n in (1, 2, 3)
    ])[0]
    assert [r.cycle for r in analyze_run(run, CVSettings())] == [1, 2, 3]
    assert [r.cycle for r in analyze_run(run, CVSettings(excluded_cycles=[1]))] == [2, 3]
    assert [r.cycle for r in analyze_run(run, CVSettings(excluded_cycles=[1]), include_excluded=True)] == [1, 2, 3]
    res = analyze_run(run, CVSettings())
    st = summarize_stability(res, 1.0)
    assert set(st) == {"i_pa", "i_pc"} and len(st["i_pa"].values) == 3


# ------------------------------------------------------------------ real data

def test_scan_rate_comes_from_the_pssession(data_dir):
    runs = group_cv_runs(load_pssession(data_dir / "2026-11-02 B2 GY data.pssession").curves)
    assert len(runs) == 1
    r = runs[0]
    assert r.cycle_numbers == [1, 2, 3, 4, 5]
    assert r.scan_rate == pytest.approx(0.05)
    assert r.meta["e_vtx2"] == pytest.approx(0.3) and r.meta["e_step"] == pytest.approx(0.005)


def test_csv_has_no_scan_rate_but_groups_the_same(data_dir):
    runs = group_cv_runs(load_csv(data_dir / "2026-11-02 B2 GY data.csv").curves)
    assert len(runs) == 1 and runs[0].cycle_numbers == [1, 2, 3, 4, 5] and runs[0].scan_rate == 0.0


def test_a6_pssession_names_its_run(data_dir):
    runs = group_cv_runs(load_pssession(data_dir / "2026-11-02 A6 GY data.pssession").curves)
    assert [r.title for r in runs] == ["A6 CV"]


def test_b2_cycles_match_hand_inspection(data_dir):
    """Values read off the raw data: anodic peak ~0.173-0.183 V growing 8.9 -> 11.6 µA,
    cathodic peak ~-0.06 V of ~-10 .. -10.9 µA; still rising ~2.4 % at cycle 5."""
    run = group_cv_runs(load_csv(data_dir / "2026-11-02 B2 GY data.csv").curves)[0]
    res = analyze_run(run, CVSettings(scan_rate=0.05))
    assert [r.cycle for r in res] == [1, 2, 3, 4, 5]
    ipa = [r.i_pa for r in res]
    assert ipa == pytest.approx([8.925, 10.333, 10.969, 11.359, 11.628], abs=0.01)
    assert all(0.17 <= r.e_pa <= 0.19 for r in res)
    assert res[0].i_pc == pytest.approx(-9.952, abs=0.01) and res[4].i_pc == pytest.approx(-10.909, abs=0.01)
    assert all(-0.08 <= r.e_pc <= -0.05 for r in res)
    assert all(math.isfinite(r.capacitance) for r in res)
    st = summarize_stability(res, 1.0)["i_pa"]
    assert st.stable_from is None                         # still conditioning at cycle 5
    assert st.last_change_pct == pytest.approx(100 * (11.628 - 11.359) / 11.359, abs=0.05)


def test_cycle_one_charge_is_distorted_by_the_startup_transient(data_dir):
    """Excluding nothing, cycle 1's anodic charge is far below the others (-140 µA start-up)."""
    run = group_cv_runs(load_csv(data_dir / "2026-11-02 B2 GY data.csv").curves)[0]
    res = analyze_run(run, CVSettings(scan_rate=0.05))
    assert res[0].q_anodic < 0.8 * res[1].q_anodic


def test_raw_current_at_the_peak_is_reported_with_a_baseline():
    e = triangle()
    up = np.diff(e, append=e[-1] + 1) > 0
    i = np.where(up, 20.0 * (e + 0.3) + gaussian(e, 0.0, 0.03, 4.0), 0.0)
    corr = analyze_cycle(e, i, CVSettings(anodic_window=[-0.2, 0.2], baseline="linear"))
    k = int(np.argmin(np.abs(e[up] - corr.e_pa)))
    assert corr.i_pa_raw == pytest.approx(i[up][k]) and corr.i_pa_raw > corr.i_pa + 1.0   # raw sits on the ramp
    plain = analyze_cycle(e, i, CVSettings())
    assert plain.i_pa_raw == plain.i_pa                                                   # no baseline: identical


def test_window_baselines_only_for_linear_mode():
    e = triangle()
    i = np.zeros_like(e)
    assert window_baselines(e, i, CVSettings()) == {}
    b = window_baselines(e, i, CVSettings(baseline="linear", anodic_window=[-0.1, 0.1], cathodic_window=[5, 6]))
    (e0, _), (e1, _) = b["anodic"]
    assert e0 == pytest.approx(-0.1, abs=0.006) and e1 == pytest.approx(0.1, abs=0.006)
    assert b["cathodic"] is None


def test_linear_baseline_is_ignored_for_an_unrestricted_window():
    """Across a whole sweep a 'baseline' is a meaningless diagonal: raw current is used instead."""
    e = triangle()
    up = np.diff(e, append=e[-1] + 1) > 0
    i = np.where(up, 20.0 * (e + 0.3) + gaussian(e, 0.0, 0.03, 4.0), -gaussian(e, -0.05, 0.03, 6.0))
    raw = analyze_cycle(e, i, CVSettings())
    lin = analyze_cycle(e, i, CVSettings(baseline="linear"))                        # no windows at all
    assert (lin.e_pa, lin.i_pa, lin.e_pc, lin.i_pc) == (raw.e_pa, raw.i_pa, raw.e_pc, raw.i_pc)
    half = analyze_cycle(e, i, CVSettings(baseline="linear", anodic_window=[-0.2, 0.2]))
    assert half.i_pa != raw.i_pa and half.i_pc == raw.i_pc                           # only the restricted peak
    assert window_baselines(e, i, CVSettings(baseline="linear")) == {"anodic": None, "cathodic": None}
