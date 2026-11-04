"""Standard addition on synthetic chronoamperograms and point sets whose true answer is known."""
import math

import numpy as np
import pytest

from echem_analyzer import presets, standard_addition as sa
from echem_analyzer.ca_analysis import analyze_ca, linear_fit, sync_step_lists
from echem_analyzer.models import CAAnalysisSettings

SEG, DT = 40.0, 0.1


def trace(cx_cell_uM, stock_mM=10.0, v0_ml=10.0, v_ul=20.0, n=5, k=0.1, bkg=0.0, noise=0.0, model="physical",
          offsets=None, seed=0):
    """A cathodic staircase. ``cx_cell_uM`` is the analyte concentration in the cell before the first addition and
    ``k`` the sensitivity in µA per µM.

    model "physical": the cell really is diluted, C = (Cx·V0 + c_s·ΣV)/(V0 + ΣV). Exact in signal-scaling mode.
    model "spec":     signal = k·(Cx + C_n) with C_n = c_s·ΣV/(V0+ΣV): exactly linear in the concentration-mode axis."""
    rng = np.random.default_rng(seed)
    t = np.arange(0, (n + 1) * SEG, DT)
    level = np.zeros(n + 1)
    for j in range(n + 1):
        nu_ml = j * v_ul / 1000.0
        added_uM = stock_mM * 1000.0 * nu_ml / (v0_ml + nu_ml)
        cell = cx_cell_uM + added_uM if model == "spec" else (cx_cell_uM * v0_ml + stock_mM * 1000.0 * nu_ml) / (v0_ml + nu_ml)
        level[j] = k * cell + bkg + (offsets[j] if offsets is not None else 0.0)
    seg = np.minimum((t // SEG).astype(int), n)
    i = -level[seg] + rng.normal(0, noise, t.size)
    s = CAAnalysisSettings(drop_last=0, stock_conc=stock_mM, stock_unit="mM", v0_ml=v0_ml, v_add_ul=v_ul, settle=3.0, window=5.0,
                           end_guard=0.5, step_times=[SEG * (j + 1) for j in range(n)], background=bkg)
    sync_step_lists(s)
    return t, i, s


# ===================================================================== known x-intercepts

@pytest.mark.parametrize("v_sample_frac, label", [(1.0, "undiluted"), (0.5, "1:1 mix"), (0.2, "1:5")])
def test_signal_scaling_mode_is_exact_with_background_subtracted(v_sample_frac, label):
    c_sample_true_uM = 40.0
    cx_cell = c_sample_true_uM * v_sample_frac
    t, i, s = trace(cx_cell, bkg=0.35)
    s.dilution_mode, s.v_sample_ml = "signal", 10.0 * v_sample_frac
    r = analyze_ca(t, i, s)
    assert r.c_x == pytest.approx(cx_cell / 1000.0, rel=1e-6)                       # mM, the stock's unit
    assert r.c_sample == pytest.approx(c_sample_true_uM / 1000.0, rel=1e-6), label
    assert r.x0 == pytest.approx(-r.c_x) and r.sensitivity == pytest.approx(100.0, rel=1e-6)      # 0.1 µA/µM = 100 µA/mM
    assert not any("Background" in w for w in r.warnings)


@pytest.mark.parametrize("v_sample_frac", [1.0, 0.5, 0.2])
def test_concentration_mode_recovers_the_spec_model_exactly(v_sample_frac):
    c_sample_true_uM = 40.0
    cx_cell = c_sample_true_uM * v_sample_frac
    t, i, s = trace(cx_cell, bkg=0.35, model="spec")
    s.v_sample_ml = 10.0 * v_sample_frac
    r = analyze_ca(t, i, s)
    assert s.dilution_mode == "concentration"
    assert r.c_x == pytest.approx(cx_cell / 1000.0, rel=1e-6)
    assert r.c_sample == pytest.approx(c_sample_true_uM / 1000.0, rel=1e-6)
    assert r.c_sample == pytest.approx(r.c_x * 10.0 / s.v_sample_ml)
    if v_sample_frac == 0.5:
        assert r.c_sample == pytest.approx(2 * r.c_x)                                # a 1:1 mix doubles it


def test_concentration_mode_on_a_really_diluted_cell_is_within_the_added_volume_fraction():
    """The honest limit of concentration mode: the sample itself is diluted by the additions, which it does not correct."""
    t, i, s = trace(40.0, bkg=0.35)                                                  # physical model, 1 % added in total
    r_conc = analyze_ca(t, i, s)
    s2 = CAAnalysisSettings.from_dict(s.to_dict())
    s2.dilution_mode = "signal"
    r_sig = analyze_ca(t, i, s2)
    ratio = 5 * 20.0 / 10000.0
    assert abs(r_conc.c_x * 1000 - 40.0) / 40.0 < ratio                               # off by less than ΣV/V0 ...
    assert abs(r_conc.c_x * 1000 - 40.0) / 40.0 > 1e-4                                # ... but not exact,
    assert r_sig.c_x * 1000 == pytest.approx(40.0, rel=1e-6)                          # while signal scaling is


def test_missing_background_shifts_cx_by_exactly_background_over_slope():
    bkg = 0.5
    t, i, s = trace(30.0, bkg=bkg, model="spec")
    right = analyze_ca(t, i, s)                                                        # background entered
    s_zero = CAAnalysisSettings.from_dict(s.to_dict())
    s_zero.background = 0.0
    wrong = analyze_ca(t, i, s_zero)
    assert right.c_x * 1000 == pytest.approx(30.0, rel=1e-6)
    assert wrong.c_x - right.c_x == pytest.approx(bkg / wrong.sensitivity, rel=1e-6)    # C_x' = C_x + β/b
    assert any("Background current is 0" in w for w in wrong.warnings) and not any("Background current is 0" in w for w in right.warnings)


def test_no_double_dilution_correction():
    assert sa.dilution_flags("concentration") == (True, False) and sa.dilution_flags("signal") == (False, True)
    assert sa.dilution_flags("none") == (False, False)
    t, i, s = trace(40.0, model="spec", bkg=0.1)
    s.dilution_mode = "signal"
    r = analyze_ca(t, i, s)
    assert r.conc_added[5] == pytest.approx(10.0 * 100e-3 / 10.0)                      # c_s·ΣV/V0 in mM: no (V0 + ΣV)
    s.dilution_mode = "concentration"
    assert analyze_ca(t, i, s).conc_added[5] == pytest.approx(10.0 * 0.1 / 10.1)


def test_legacy_projects_with_both_switches_on_keep_one_correction():
    both = CAAnalysisSettings.from_dict({"dilution_conc": True, "dilution_signal": True, "stock_conc": 5.0})
    assert both.dilution_mode == "concentration" and both.stock_conc == 5.0
    assert CAAnalysisSettings.from_dict({"dilution_conc": False, "dilution_signal": True}).dilution_mode == "signal"
    assert CAAnalysisSettings.from_dict({"dilution_conc": False, "dilution_signal": False}).dilution_mode == "none"
    assert CAAnalysisSettings.from_dict({}).dilution_mode == "concentration"
    assert CAAnalysisSettings().sigma_source == "fit" and CAAnalysisSettings().lod_k == 3.3 and CAAnalysisSettings().suitability_min == 12.0


# ===================================================================== statistics

def test_uncertainty_of_x0_matches_the_formula_and_the_interval_covers():
    rng = np.random.default_rng(5)
    x = np.array([0.0, 1, 2, 3, 4, 5])
    y = 2.0 * (x + 3.0) + rng.normal(0, 0.2, x.size)
    f = linear_fit(x, y)
    n, sxx, ybar = len(x), np.sum((x - x.mean()) ** 2), y.mean()
    s_y = math.sqrt(np.sum(f.residuals ** 2) / (n - 2))
    s_x0 = s_y / abs(f.slope) * math.sqrt(1 / n + ybar ** 2 / (f.slope ** 2 * sxx))
    from scipy import stats
    assert f.x_intercept == pytest.approx(-f.intercept / f.slope) and f.x_intercept_se == pytest.approx(s_x0)
    assert f.t_crit == pytest.approx(stats.t.ppf(0.975, n - 2)) and f.x_intercept_ci95 == pytest.approx(f.t_crit * s_x0)
    assert f.suitability == pytest.approx(abs(f.slope) * math.sqrt(sxx) / s_y) and f.sxx == pytest.approx(sxx)

    hits, trials = 0, 1500
    s = CAAnalysisSettings(stock_conc=1.0, v0_ml=10.0, background=1.0)
    for _ in range(trials):
        yy = 2.0 * (x + 3.0) + rng.normal(0, 0.2, x.size)
        fig = sa.evaluate_points(x, yy, np.ones(x.size, bool), s)
        hits += abs(fig.c_x - 3.0) <= fig.c_x_ci95
    assert 0.93 <= hits / trials <= 0.97                                              # a 95 % interval covers about 95 %


def test_sample_uncertainty_scales_with_the_volume_ratio():
    x = np.array([0.0, 1, 2, 3, 4, 5])
    y = 2.0 * (x + 3.0) + np.array([0.05, -0.04, 0.02, -0.03, 0.04, -0.02])
    s = CAAnalysisSettings(stock_conc=1.0, v0_ml=10.0, v_sample_ml=2.0, background=1.0)
    fig = sa.evaluate_points(x, y, np.ones(6, bool), s)
    assert fig.c_sample == pytest.approx(5 * fig.c_x) and fig.c_sample_se == pytest.approx(5 * fig.c_x_se)
    assert fig.c_sample_ci95 == pytest.approx(5 * fig.c_x_ci95)


def test_three_sigma_sources_and_the_lod_factor():
    t, i, s = trace(40.0, model="spec", bkg=0.2, noise=0.01, seed=3)
    s.blank_replicates = [0.0100, 0.0125, 0.0108, 0.0121]
    r = analyze_ca(t, i, s)
    b = abs(r.fit.slope)
    assert set(r.sigmas) == {"fit", "baseline", "blank"} and all(math.isfinite(v) for v in r.sigmas.values())
    assert r.sigmas["blank"] == pytest.approx(np.std(s.blank_replicates, ddof=1))
    assert r.sigma_used == "fit" and r.sigma == pytest.approx(r.fit.s_yx)             # the default is s(y/x)
    for key in ("fit", "baseline", "blank"):
        assert r.lods[key] == pytest.approx(3.3 * r.sigmas[key] / b) and r.loqs[key] == pytest.approx(10 * r.sigmas[key] / b)
    assert r.lod == pytest.approx(r.lods["fit"])
    for source in ("baseline", "blank"):
        s.sigma_source = source
        rr = analyze_ca(t, i, s)
        assert rr.sigma_used == source and rr.lod == pytest.approx(rr.lods[source])
    s.sigma_source, s.lod_k, s.loq_k = "fit", 3.0, 11.0
    rr = analyze_ca(t, i, s)
    assert rr.lod == pytest.approx(3.0 * rr.sigma / b) and rr.loq == pytest.approx(11.0 * rr.sigma / b)


def test_unavailable_sigma_falls_back_and_says_so():
    t, i, s = trace(40.0, model="spec", bkg=0.2, noise=0.01)
    s.sigma_source = "blank"                                                          # no replicates entered
    r = analyze_ca(t, i, s)
    assert r.sigma_used == "fit" and any("blank replicates is not available" in w for w in r.warnings)


def test_both_ways_with_and_without_the_baseline_point():
    t, i, s = trace(40.0, model="spec", bkg=0.2, noise=0.02, seed=4)
    s.compare_baseline = True
    r = analyze_ca(t, i, s)
    assert r.alt is not None and r.alt["include_baseline"] is False and r.alt["n"] == r.fit.n - 1
    f = linear_fit(r.conc_added[1:], r.signal[1:])
    assert r.alt["c_x"] == pytest.approx(abs(f.x_intercept)) and r.alt["c_x_ci95"] == pytest.approx(f.x_intercept_ci95)
    s.include_baseline, s.compare_baseline = False, True
    r2 = analyze_ca(t, i, s)
    assert r2.alt["include_baseline"] is True and r2.alt["c_x"] == pytest.approx(r.c_x)
    s.compare_baseline = False
    assert analyze_ca(t, i, s).alt is None


def test_verified_volume_replaces_the_nominal_one():
    t, i, s = trace(40.0, v_ul=20.0)
    s.verified_ul = 19.0
    r = analyze_ca(t, i, s)
    assert r.volumes_ul == [19.0] * 5 and r.conc_added[1] == pytest.approx(10.0 * 19e-3 / (10.0 + 19e-3))
    assert any("verified volume (19 µL) differs" in w for w in r.warnings)
    s.step_volumes_ul[2] = 25.0                                                       # a typed volume for one step is kept
    assert analyze_ca(t, i, s).volumes_ul == [19.0, 19.0, 25.0, 19.0, 19.0]
    s.verified_ul = 0.0
    assert analyze_ca(t, i, s).volumes_ul == [20.0, 20.0, 25.0, 20.0, 20.0]


def test_units_are_explicit_and_never_guessed():
    assert sa.to_canonical(10000, "µL", sa.VOLUME_ML) == pytest.approx(10.0) and sa.from_canonical(10.0, "µL", sa.VOLUME_ML) == pytest.approx(10000)
    assert sa.to_canonical(250, "nA", sa.CURRENT_UA) == pytest.approx(0.25) and sa.to_canonical(0.5, "mA", sa.CURRENT_UA) == pytest.approx(500)
    with pytest.raises(ValueError, match="unknown unit"):
        sa.to_canonical(1.0, "L", sa.VOLUME_ML)
    with pytest.raises(ValueError):
        sa.factor("", sa.CURRENT_UA)


# ===================================================================== the other callers

def test_evaluate_points_is_reusable_for_any_signal_such_as_peak_heights():
    conc = np.array([0.0, 5, 10, 15, 20])                                              # µM added
    peak = 1.5 + 0.2 * conc                                                           # µA peak heights: x0 = -7.5
    s = CAAnalysisSettings(stock_conc=100.0, stock_unit="µM", v0_ml=5.0, v_sample_ml=2.5, background=0.1, include_baseline=True)
    fig = sa.evaluate_points(conc, peak, np.ones(5, bool), s)
    assert fig.x0 == pytest.approx(-7.5) and fig.c_x == pytest.approx(7.5) and fig.c_sample == pytest.approx(15.0)
    assert fig.fit.r2 == pytest.approx(1.0) and fig.n_used == fig.n_expected == 5
    assert math.isinf(fig.suitability)                                                # no scatter: perfectly suitable, flagged by nothing
    assert not [w for w in fig.warnings if "Suitability" in w]


# ===================================================================== warnings

def clean_case():
    t, i, s = trace(40.0, model="spec", bkg=0.3, noise=0.002, seed=11)
    return t, i, s


def test_a_good_run_raises_no_warning_at_all():
    t, i, s = clean_case()
    r = analyze_ca(t, i, s)
    assert r.warnings == [] and r.suitability > 12 and r.n_expected == r.fit.n == 6


def test_warning_sample_volume_larger_than_the_cell_and_dilution_off():
    t, i, s = clean_case()
    s.v_sample_ml = 12.0
    assert any("larger than the cell volume" in w for w in analyze_ca(t, i, s).warnings)
    t, i, s = trace(40.0, model="spec", bkg=0.3, noise=0.002, v_ul=300.0, n=5)        # 15 % of V0 in total
    s.dilution_mode = "none"
    assert any("Dilution correction is off" in w for w in analyze_ca(t, i, s).warnings)
    s.dilution_mode = "concentration"
    assert any("does not correct the dilution of the sample itself" in w or "not the dilution" in w or "dilution of the sample itself" in w
               for w in analyze_ca(t, i, s).warnings)
    s.dilution_mode = "signal"
    assert not any("dilution" in w.lower() for w in analyze_ca(t, i, s).warnings)


def test_warning_when_the_result_is_implausible_for_the_stock_unit():
    t, i, s = clean_case()
    s.plausible_max = 0.01                                                            # mM; the true C_sample is 0.04 mM
    assert any("plausible maximum" in w for w in analyze_ca(t, i, s).warnings)
    s.plausible_max = 0.0
    x = np.array([0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    s2 = CAAnalysisSettings(stock_conc=0.5, stock_unit="mM", v0_ml=10.0, background=1.0)
    fig = sa.evaluate_points(x, 5 + 5 * x, np.ones(6, bool), s2)                      # C_x = 1 mM > the 0.5 mM stock
    assert any("larger than the stock itself" in w for w in fig.warnings)


def test_warning_for_curved_response_and_not_for_random_scatter():
    x = np.arange(6.0)
    s = CAAnalysisSettings(stock_conc=1.0, v0_ml=10.0, background=1.0)
    curved = 2.0 * x + 0.45 * x ** 2 + 5.0
    fig = sa.evaluate_points(x, curved + np.array([0.02, -0.01, 0.015, -0.02, 0.01, -0.005]), np.ones(6, bool), s)
    assert any("Residuals show a pattern" in w for w in fig.warnings)
    rng = np.random.default_rng(1)
    flagged = sum(any("Residuals show a pattern" in w for w in sa.evaluate_points(x, 5 + 2 * x + rng.normal(0, 0.1, 6), np.ones(6, bool), s).warnings)
                  for _ in range(400))
    assert flagged / 400 < 0.12                                                       # few false alarms on pure noise
    assert sa.residual_pattern([0.1, -0.1, 0.0], 0.1) is None                          # too few points to say


def test_warning_when_the_suitability_check_fails_and_the_threshold_is_adjustable():
    x = np.array([0.0, 1, 2, 3, 4, 5])
    noisy = 5 + 1.0 * x + np.array([0.9, -1.1, 0.8, -0.7, 1.2, -1.0])
    s = CAAnalysisSettings(stock_conc=1.0, v0_ml=10.0, background=1.0)
    fig = sa.evaluate_points(x, noisy, np.ones(6, bool), s)
    assert fig.suitability < 12 and any("Suitability" in w for w in fig.warnings)
    s.suitability_min = fig.suitability / 2
    assert not any("Suitability" in w for w in sa.evaluate_points(x, noisy, np.ones(6, bool), s).warnings)


def test_warning_when_baseline_noise_lod_is_much_smaller_than_the_fit_lod():
    offsets = [0.0, 0.03, -0.04, 0.035, -0.03, 0.025]                                  # real scatter between plateaus
    t, i, s = trace(40.0, model="spec", bkg=0.3, noise=0.0005, offsets=offsets, seed=2)
    r = analyze_ca(t, i, s)
    assert r.lods["baseline"] < r.lods["fit"] / 3 and any("LOD from baseline noise" in w for w in r.warnings)


def test_warning_when_the_points_in_the_fit_do_not_match_the_additions():
    t, i, s = clean_case()
    s.step_included[2] = False
    r = analyze_ca(t, i, s)
    assert r.fit.n == 5 and r.n_expected == 6 and any("fit uses 5 points" in w and "6 expected" in w for w in r.warnings)
    s.step_included[2] = True
    s.include_baseline = False                                                         # baseline off: expect additions only
    r = analyze_ca(t, i, s)
    assert r.fit.n == 5 and r.n_expected == 5 and not any("fit uses" in w for w in r.warnings)


def test_warning_when_settle_plus_window_is_longer_than_a_segment():
    t, i, s = clean_case()
    s.settle, s.window = 30.0, 20.0                                                    # 50 s needed, segments are 40 s
    assert any("longer than the segment" in w for w in analyze_ca(t, i, s).warnings)
    s.settle, s.window = 3.0, 5.0
    assert not any("longer than the segment" in w for w in analyze_ca(t, i, s).warnings)


def test_warnings_never_block_the_calculation():
    t, i, s = trace(40.0, model="spec", bkg=0.0, noise=0.002)
    s.v_sample_ml, s.dilution_mode = 15.0, "none"
    r = analyze_ca(t, i, s)
    assert len(r.warnings) >= 3 and r.fit is not None and math.isfinite(r.c_x) and math.isfinite(r.lod)


# ===================================================================== presets

NEW_FIELDS = ("verified_ul", "dilution_mode", "v0_unit", "v_add_unit", "verified_unit", "v_sample_unit", "bkg_unit", "sigma_source", "blank_replicates",
              "lod_k", "loq_k", "suitability_min", "plausible_max", "compare_baseline")


def test_new_settings_are_saved_in_presets_and_reapplied():
    assert set(NEW_FIELDS) <= set(presets.CA_FIELDS)
    a = CAAnalysisSettings(stock_conc=5.0, v0_ml=2.0, v0_unit="µL", v_add_unit="mL", bkg_unit="nA", verified_ul=9.6, dilution_mode="signal",
                           sigma_source="blank", blank_replicates=[0.01, 0.012, 0.011], lod_k=3.0, loq_k=9.0, suitability_min=8.0,
                           plausible_max=100.0, compare_baseline=True, background=0.05, v_sample_ml=1.0)
    preset = presets.ca_preset_from_settings(a)
    import json
    preset = json.loads(json.dumps(preset))                                            # as it goes through the JSON file
    b = CAAnalysisSettings()
    assert presets.apply_ca_preset(b, preset) == []
    for k in presets.CA_FIELDS:
        assert getattr(b, k) == getattr(a, k), k


def test_invalid_preset_values_are_ignored_and_old_presets_still_load():
    s = CAAnalysisSettings()
    bad = {"dilution_mode": "both", "bkg_unit": "A", "v0_unit": "L", "sigma_source": "magic", "blank_replicates": "x", "lod_k": "three"}
    assert set(presets.apply_ca_preset(s, bad)) == set(bad)
    assert s.dilution_mode == "concentration" and s.sigma_source == "fit" and s.lod_k == 3.3
    old = {"stock_conc": 2.0, "dilution_conc": False, "dilution_signal": True, "sigma_source": "baseline"}
    assert presets.apply_ca_preset(s, old) == [] and s.dilution_mode == "signal" and s.sigma_source == "baseline"
