"""Ion-selective electrodes: data generated from the Nikolsky–Eisenman equation, so the right answers are known."""
import math

import numpy as np
import pytest

from echem_analyzer import calibration as cal_module
from echem_analyzer import potentiometry as pot

S25 = 59.16                                          # mV/decade, z = 1, 25 °C


def ise_response(a, a_lod=2e-6, e0=210.0, s=S25, noise=0.2, seed=3):
    """E = E0 + S·log10(a + a_lod): Nernstian well above a_lod, flat well below it (the asymptotes cross at a_lod)."""
    a = np.asarray(a, float)
    return e0 + s * np.log10(a + a_lod) + np.random.default_rng(seed).normal(0, noise, a.size)


LEVELS = 10 ** np.arange(-8, -0.9, 0.5)                                # 1e-8 ... 1e-1 M in half-decade steps


# ===================================================================== activity

def test_davies_activity_coefficients_match_the_published_values():
    assert pot.debye_huckel_a(25.0) == pytest.approx(0.511, abs=0.003)
    assert pot.activity_coefficient(1, 0.1) == pytest.approx(0.78, abs=0.01)           # 0.1 M monovalent: 0.78
    assert pot.activity_coefficient(2, 0.01) == pytest.approx(0.66, abs=0.01)          # 0.01 M divalent: 0.66
    assert pot.activity_coefficient(1, 0.0) == 1.0
    assert pot.activity_coefficient(2, 0.05) < pot.activity_coefficient(1, 0.05) < 1.0  # higher charge, stronger screening
    assert pot.activity_coefficient(1, 0.1, 37.0) != pot.activity_coefficient(1, 0.1, 25.0)
    with pytest.raises(ValueError):
        pot.activity_coefficient(1, -0.1)


# ===================================================================== the Nernstian region and the detection limit

def test_a_curve_with_a_plateau_gives_the_nernstian_region_and_the_iupac_limit():
    fit = pot.fit_ise(LEVELS, ise_response(LEVELS), z=1)
    assert fit.ok and fit.lod_found and fit.plateau is not None
    assert fit.slope == pytest.approx(S25, rel=0.01) and fit.slope_pct == pytest.approx(100.0, abs=1.5)
    assert fit.lod == pytest.approx(2e-6, rel=0.25)                                     # the crossing of the two asymptotes (the corner biases it slightly high)
    assert abs(fit.plateau[0]) < 0.5 * S25                                              # the low part is flat
    assert fit.c_low > 10 * fit.lod and fit.c_high == pytest.approx(LEVELS.max())          # the corner is left out of the Nernstian region
    assert fit.nernst == pytest.approx(S25, abs=0.01) and fit.r2 > 0.999
    assert set(fit.index) == set(np.flatnonzero(LEVELS >= fit.c_low * 0.999))


def test_the_slope_and_limit_are_accurate_whatever_the_noise():
    slopes, lods = [], []
    for seed in range(30):
        fit = pot.fit_ise(LEVELS, ise_response(LEVELS, seed=seed, noise=0.3))
        assert fit.lod_found
        slopes.append(fit.slope)
        lods.append(fit.lod)
    assert np.mean(slopes) == pytest.approx(S25, rel=0.01) and np.std(slopes) < 0.5
    assert np.median(lods) == pytest.approx(2e-6, rel=0.25)


def test_one_wrong_standard_does_not_let_the_curved_corner_into_the_fit():
    e = ise_response(LEVELS, noise=0.1)
    clean = pot.fit_ise(LEVELS, e)
    e_bad = e.copy()
    e_bad[12] += 12.0                                                                   # the 1e-2 M standard is 12 mV off
    bad = pot.fit_ise(LEVELS, e_bad)
    assert bad.lod_found and bad.c_low == pytest.approx(clean.c_low)                    # the same region: the corner is still left out
    assert bad.slope == pytest.approx(S25, rel=0.03) and 12 in bad.index               # the bad point stays in, to be found by the diagnostics
    assert bad.lod == pytest.approx(clean.lod, rel=0.15)


def test_a_straight_curve_has_no_detection_limit_to_read_off():
    c = 10 ** np.arange(-5, -0.9, 0.5)
    fit = pot.fit_ise(c, ise_response(c, a_lod=1e-12))
    assert fit.ok and not fit.lod_found and fit.plateau is None and fit.c_low == pytest.approx(c.min())
    assert "below the range measured" in fit.message and len(fit.index) == len(c)


def test_slope_and_sign_for_anions_and_temperature():
    fit = pot.fit_ise(LEVELS, ise_response(LEVELS, s=-S25), z=-1)
    assert fit.slope == pytest.approx(-S25, rel=0.01) and fit.slope_pct == pytest.approx(100.0, abs=1.5)
    assert pot.slope_remarks(fit, -1, 25.0) == []
    wearable = pot.fit_ise(LEVELS, ise_response(LEVELS, s=61.54), z=1, temp_c=37.0)
    assert wearable.nernst == pytest.approx(61.54, abs=0.01) and wearable.slope_pct == pytest.approx(100.0, abs=1.5)
    assert pot.fit_ise(LEVELS, ise_response(LEVELS, s=61.54), z=1, temp_c=25.0).slope_pct > 103      # judged against the wrong temperature


def test_remarks_name_a_wrong_sign_a_sub_nernstian_and_a_super_nernstian_slope():
    sub_ = pot.fit_ise(LEVELS, ise_response(LEVELS, s=45.0), z=1)
    (remark,) = pot.slope_remarks(sub_, 1, 25.0)
    assert "76 %" in remark and "sub-Nernstian" in remark
    assert any("positive" in r and "anion" in r for r in pot.slope_remarks(pot.fit_ise(LEVELS, ise_response(LEVELS)), -1, 25.0))
    assert any("super-Nernstian" in r for r in pot.slope_remarks(pot.fit_ise(LEVELS, ise_response(LEVELS, s=75.0)), 1, 25.0))
    assert pot.slope_remarks(pot.ISEFit(), 1, 25.0) == []


def test_a_response_that_saturates_at_the_top_ends_the_nernstian_region_there():
    c = 10 ** np.arange(-6, -0.9, 0.5)
    e = 210 + S25 * np.log10(c)
    e = np.minimum(e, 210 + S25 * -3.0)                                                 # flat above 1e-3
    fit = pot.fit_ise(c, e + np.random.default_rng(1).normal(0, 0.1, e.size))
    assert fit.ok and not fit.lod_found and fit.plateau is None
    assert fit.saturation_above == pytest.approx(1e-3, rel=0.4) and fit.c_high <= 1.1e-3
    assert "flattens above" in fit.message


def test_replicates_zero_and_negative_concentrations_and_too_little_data():
    c = np.repeat(LEVELS, 2)
    e = ise_response(c, seed=5)
    fit = pot.fit_ise(np.r_[c, 0.0, -1.0], np.r_[e, 0.0, 0.0])                          # a zero and a negative concentration cannot be logged
    assert fit.ok and fit.lod_found and len(fit.index) < len(c) + 2 and (fit.index < len(c)).all()
    too_few = pot.fit_ise([1e-3, 1e-2], [100, 160])
    assert not too_few.ok and "at least three" in too_few.message
    assert not pot.fit_ise([1e-3, 1e-3, 1e-3], [100, 101, 99]).ok


# ===================================================================== selectivity

@pytest.mark.parametrize("z_i,z_j,log_k", [(1, 1, -2.0), (1, 1, -3.5), (1, 2, -1.2), (2, 1, -2.7), (1, -1, -4.0)])
def test_separate_solution_method_recovers_the_selectivity_coefficient(z_i, z_j, log_k):
    a_i, a_j = 0.01, 0.05
    s_per_decade = S25 / z_i
    e_i = 210 + s_per_decade * math.log10(a_i)
    e_j = 210 + s_per_decade * math.log10((10 ** log_k) * a_j ** (z_i / z_j))           # what Nikolsky–Eisenman predicts in pure j
    assert pot.log_k_ssm(e_i, e_j, a_i, a_j, z_i, z_j) == pytest.approx(log_k, abs=0.02)


def test_fixed_interference_method_reads_k_off_the_crossing():
    a_j, true_log_k = 0.1, -2.0                                                         # K·a_j = 1e-3: the plateau meets the line at a_i = 1e-3
    a_i = 10 ** np.arange(-6, -0.4, 0.25)
    e = 210 + S25 * np.log10(a_i + (10 ** true_log_k) * a_j) + np.random.default_rng(2).normal(0, 0.1, a_i.size)
    fit = pot.fit_ise(a_i, e)
    assert fit.lod_found
    assert pot.log_k_fim(fit, a_j, 1, 1) == pytest.approx(true_log_k, abs=0.3)
    assert math.isnan(pot.log_k_fim(pot.fit_ise(10 ** np.arange(-5, -1, 0.5), 210 + S25 * np.arange(-5, -1, 0.5)), a_j, 1, 1))
    assert math.isnan(pot.log_k_ssm(100, 80, 0.0, 0.1, 1, 1)) and math.isnan(pot.log_k_ssm(100, 80, 0.01, 0.1, 0, 1))


# ===================================================================== in the sensor study

def _ise_responses(a_m=LEVELS, unit="mV", gamma=1.0, samples=(), seed=3, scale=1.0, **kw):
    """Standards in µM (the activity is gamma x the concentration) with the response in `unit`; samples are potentials in mV."""
    from echem_analyzer.models import Response
    e = ise_response(np.asarray(a_m) * gamma, seed=seed, **kw) * scale
    rs = [Response(id=f"s{i}", source_label=f"std {c * 1e6:.3g} µM", quantity="E", unit=unit, value=float(v), conc=float(c * 1e6), conc_unit="µM",
                   group="ISE1", role="standard") for i, (c, v) in enumerate(zip(a_m, e))]
    rs += [Response(id=f"u{i}", source_label=f"sample {i + 1}", quantity="E", unit=unit, value=float(v) * scale, role="sample", group="ISE1")
           for i, v in enumerate(samples)]
    return rs


def _run(rs, **kw):
    from echem_analyzer import study_analysis as sa
    return sa.run_calibration(rs, sa.CalibrationOptions(quantity="E", potentiometric=True, **kw))


def test_the_study_reports_slope_detection_limit_and_range_of_an_electrode():
    cs = _run(_ise_responses(samples=[210 + S25 * math.log10(3e-4)]))
    assert cs.ok and cs.model == "loglinear" and cs.ise.lod_found
    assert cs.sensitivity == pytest.approx(S25, rel=0.01) and cs.sensitivity_unit == "mV/decade"
    assert cs.lod == pytest.approx(2.0, rel=0.25) and cs.conc_unit == "µM"            # 2e-6 M
    assert math.isnan(cs.loq) and cs.fom.range_low > 10 * cs.lod and cs.fom.n_range >= 4
    assert not any("Nernstian slope for z" in w for w in cs.warnings)                  # the generic log-linear remarks are replaced
    (s,) = cs.samples
    assert s.conc == pytest.approx(300.0, rel=0.03) and s.lo < s.conc < s.hi          # 3e-4 M = 300 µM, with its interval


def test_potentials_in_volts_are_converted_and_a_wrong_unit_is_questioned():
    cs = _run(_ise_responses(unit="V", scale=1e-3))
    assert cs.y_unit == "mV" and cs.sensitivity == pytest.approx(S25, rel=0.01)
    bad = _run(_ise_responses(unit="µA"))
    assert any("expects potentials in mV or V" in w for w in bad.warnings)


def test_the_standard_potential_needs_the_activity_coefficient_when_the_background_is_ionic():
    from echem_analyzer.potentiometry import activity_coefficient
    gamma = activity_coefficient(1, 0.1)                                                # standards in a 0.1 M background
    rs = _ise_responses(gamma=gamma, noise=0.05)
    corrected, ideal = _run(rs, ionic_strength=0.1), _run(rs)
    assert corrected.gamma == pytest.approx(gamma) and ideal.gamma == 1.0
    assert corrected.e0_mv == pytest.approx(210.0, abs=2.0)                             # the E0 the electrode really has
    assert ideal.e0_mv == pytest.approx(210.0 + S25 * math.log10(gamma), abs=2.0)       # without the correction it is off by S·log γ (6 mV)
    assert corrected.sensitivity == pytest.approx(ideal.sensitivity)                   # the slope does not depend on γ


def test_a_reading_is_described_by_where_it_falls_below_the_limit_in_the_curved_foot_or_above_the_region():
    plateau = 210 + S25 * math.log10(2e-6)                                              # the flat part sits at the detection limit by definition
    cs = _run(_ise_responses(samples=[plateau - 6.0, 210 + S25 * math.log10(1e-5), 210 + S25 * math.log10(3e-3), 210 + S25 * math.log10(0.5)]))
    text = {s.label: [w for w in cs.warnings if f"“{s.label}”" in w] for s in cs.samples}
    assert len(text["sample 1"]) == 1 and "below the detection limit" in text["sample 1"][0] and "not as a value" in text["sample 1"][0]
    assert len(text["sample 2"]) == 1 and "curved there" in text["sample 2"][0] and "approximate" in text["sample 2"][0]
    assert text["sample 3"] == []                                                       # inside the region: nothing to say
    assert len(text["sample 4"]) == 1 and "above the Nernstian region" in text["sample 4"][0]


def test_a_clean_electrode_calibration_flags_no_standard_for_sub_millivolt_curvature():
    rs = _ise_responses()                                                               # quiet data (0.2 mV): the foot of the region is off by ~0.5 mV
    cs = _run(rs)
    assert cs.diagnostics is not None and cs.flagged_points() == []
    strict = cal_module.diagnostics(cs.fit)                                             # without the minimum, the same data name a standard
    assert strict.flagged, "the test data should show the effect the minimum residual is there to remove"


def test_a_wrong_charge_and_too_few_standards_are_explained():
    cs = _run(_ise_responses(), z=-1)
    assert any("positive" in w and "anion" in w for w in cs.warnings)
    few = _run(_ise_responses(a_m=[1e-4, 1e-3]))
    assert not few.ok and any("two or more different" in w or "at least three" in w for w in few.warnings)


def test_standard_indices_refer_to_the_right_response_inside_the_nernstian_region():
    rs = _ise_responses(noise=0.1)
    k = 12
    rs[k].value += 12.0                                                                 # one standard in the Nernstian region is wrong
    cs = _run(rs)
    flagged = cs.flagged_points()
    assert [cs.labels[i] for i, _ in flagged] == [rs[k].source_label]
    assert len(cs.fit_index) == len(cs.fit.x) and cs.x[cs.fit_index[0]] == pytest.approx(cs.fit.x[0])


# ===================================================================== in the Study tab and the report

def test_the_calibration_panel_switches_to_electrode_mode_and_remembers_it(qapp, tmp_path):
    from helpers import make_window
    win = make_window(tmp_path / "w")
    win.session.responses[:] = _ise_responses(samples=[210 + S25 * math.log10(3e-4)])
    win.session.responses_changed.emit()
    panel = win.study_tab.calibration
    assert panel.form.isRowVisible(panel.model) and not panel.form.isRowVisible(panel.z)              # off: the usual settings
    panel.ise.setChecked(True)
    assert panel.study.ise is not None and panel.form.isRowVisible(panel.z) and not panel.form.isRowVisible(panel.model)
    labels = [card.label.text() for card in panel.kpis if not card.isHidden()]
    assert labels == ["SLOPE", "DETECTION LIMIT", "NERNSTIAN REGION", "E° (A = 1 M)"]
    assert panel.kpis[0].value.text().startswith("+58.") and "of 59.2" in panel.kpis[0].sub.text()
    assert win.session.extras["study"]["calibration"]["potentiometric"] is True
    panel.z.setValue(-1)                                                                # the wrong charge: the app says so
    assert any("anion" in w for w in panel.study.warnings) and "anion" in panel.notice.text()
    panel.z.setValue(1)
    win.session.responses_changed.emit()                                                # a refresh restores the stored choice
    assert panel.ise.isChecked() and panel.z.value() == 1
    panel.ise.setChecked(False)
    assert panel.study.ise is None and panel.form.isRowVisible(panel.model)


def test_the_report_states_an_electrode_calibration_in_electrode_terms():
    from echem_analyzer import study_analysis as sa, study_report
    cs = sa.run_calibration(_ise_responses(), sa.CalibrationOptions(quantity="E", potentiometric=True, ionic_strength=0.1))
    settings, results = study_report.calibration_rows(cs)
    assert ("Mode", "ion-selective electrode, z = +1, 25 °C, background ionic strength 0.1 M (Davies)") in settings
    keys = [k for k, _ in results]
    assert keys == ["Standards", "Slope", "Nernstian region", "Detection limit (IUPAC)", "E° (a = 1 M)", "Activity coefficient"]
    slope = dict(results)["Slope"]
    assert slope.startswith("+58.") and "% of the theoretical 59.16" in slope
    assert "µM" in dict(results)["Detection limit (IUPAC)"]
    assert "LOQ" not in keys                                                            # an electrode has no LOQ in this sense


def test_the_report_uses_the_stored_charge_and_temperature_not_the_defaults():
    from echem_analyzer import study_report
    from echem_analyzer.models import Response
    e = ise_response(LEVELS, s=-61.54)                                                  # an anion electrode held at 37 °C
    rs = [Response(id=f"s{i}", source_label=f"std {i}", quantity="E", unit="mV", value=float(v), conc=float(c * 1e6), conc_unit="µM",
                   group="A1", role="standard") for i, (c, v) in enumerate(zip(LEVELS, e))]
    stored = {"calibration": {"quantity": "E", "potentiometric": True, "z": -1, "temp_c": 37.0}}
    study = study_report.compute_study(rs, {"study": stored})
    (cs,) = study.calibrations
    assert cs.options.z == -1 and cs.options.temp_c == 37.0 and cs.ise.nernst == pytest.approx(61.54, abs=0.01)
    assert cs.ise.slope_pct == pytest.approx(100.0, abs=1.5) and not any("anion" in w for w in cs.warnings)
    wrong = study_report.compute_study(rs, {"study": {"calibration": {"quantity": "E", "potentiometric": True, "z": 0, "temp_c": 900}}})
    assert wrong.calibrations[0].options.z == 1 and wrong.calibrations[0].options.temp_c == 25.0       # junk in a hand-edited project
    zero = study_report.compute_study(rs, {"study": {"calibration": {"quantity": "E", "potentiometric": True, "z": -1, "temp_c": 0.0}}})
    assert zero.calibrations[0].options.temp_c == 0.0                                    # 0 °C is a real temperature
