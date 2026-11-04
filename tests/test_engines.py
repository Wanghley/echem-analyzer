"""Engine tests against synthetic data with KNOWN answers (no real files exist for these techniques yet)."""
import math

import numpy as np
import pytest
from scipy import stats

from echem_analyzer import calibration as cal
from echem_analyzer import eis, electrode, peaks, studies, techniques, transients

RNG = np.random.default_rng(42)


# ===================================================================== technique recognition

@pytest.mark.parametrize("title,method,expected", [
    ("CA i vs t [1]", "", "CA"), ("Chronoamperometry", "", "CA"), ("", "ad", "CA"), ("", "pad", "CA"),
    ("CV i vs E Scan 2", "", "CV"), ("A6 CV", "cv", "CV"), ("Cyclic Voltammetry", "", "CV"),
    ("DPV i vs E", "", "DPV"), ("Differential Pulse Voltammetry", "", "DPV"), ("whatever", "dpv", "DPV"),
    ("SWV net current", "", "SWV"), ("Square Wave Voltammetry", "", "SWV"), ("", "swv", "SWV"),
    ("LSV i vs E", "", "LSV"), ("Linear Sweep Voltammetry", "", "LSV"), ("Normal Pulse", "", "NPV"),
    ("OCP E vs t", "", "OCP"), ("Open Circuit Potential", "", "OCP"), ("Potentiometry", "", "OCP"),
    ("Chronopotentiometry E vs t", "", "CP"), ("Chronocoulometry Q vs t", "", "CC"),
    ("Impedance spectroscopy", "", "EIS"), ("Nyquist", "", "EIS"), ("Z' vs -Z''", "", "EIS"), ("", "ei", "EIS"),
    ("calibration", "", "OTHER"), ("std addition GY 505.1mM", "", "OTHER"), ("", "", "OTHER"),
])
def test_classify(title, method, expected):
    assert techniques.classify(title, method) == expected


def test_method_id_wins_over_the_title():
    assert techniques.classify("CV scan 1", "dpv") == "DPV"


def test_every_technique_is_described():
    for key in techniques.ORDER:
        t = techniques.TECHNIQUES[key]
        assert t.label and t.family in {"voltammetry", "amperometry", "impedance", "potentiometry", "other"}


# ===================================================================== calibration

def test_linear_fit_matches_scipy():
    x = np.array([0, 1, 2, 3, 4, 5.0])
    y = 2.0 * x + 1.0 + RNG.normal(0, 0.2, x.size)
    f, ref = cal.fit_linear(x, y), stats.linregress(x, y)
    assert f.p("slope") == pytest.approx(ref.slope) and f.p("intercept") == pytest.approx(ref.intercept)
    assert f.se("slope") == pytest.approx(ref.stderr) and f.se("intercept") == pytest.approx(ref.intercept_stderr)
    assert f.r2 == pytest.approx(ref.rvalue ** 2)


def test_weights_pull_the_line_towards_the_trusted_points():
    x = np.array([1, 2, 3, 4.0])
    y = np.array([1.0, 2.0, 3.0, 10.0])                                  # the last point is off
    plain, weighted = cal.fit_linear(x, y), cal.fit_linear(x, y, weights=[1, 1, 1, 1e-6])
    assert weighted.p("slope") == pytest.approx(1.0, abs=1e-3) and plain.p("slope") > 2.0


def test_loglinear_recovers_a_nernstian_slope():
    c = np.logspace(-6, -2, 9)
    e = 250.0 - cal.nernst_slope(1) * np.log10(c) * -1                      # E = 250 + 59.16·log10 c  (mV)
    f = cal.fit_loglinear(c, e)
    assert f.p("slope") == pytest.approx(59.16, abs=0.02) and f.p("intercept") == pytest.approx(250.0, abs=0.05)
    assert f.predict(1e-4) == pytest.approx(250.0 + 59.16 * -4, abs=0.1)


def test_michaelis_menten_recovers_parameters():
    c = np.array([1, 2, 5, 10, 20, 50, 100, 200.0])
    y = 80.0 * c / (25.0 + c) * (1 + RNG.normal(0, 0.02, c.size))
    f = cal.fit_michaelis(c, y)
    assert f.ok and f.p("ymax") == pytest.approx(80.0, rel=0.1) and f.p("K") == pytest.approx(25.0, rel=0.15)
    assert f.r2 > 0.99 and f.se("K") > 0


def test_hill_recovers_the_cooperativity():
    c = np.logspace(-1, 2, 14)
    y = 10.0 * c ** 1.6 / (5.0 ** 1.6 + c ** 1.6) + RNG.normal(0, 0.05, c.size)
    f = cal.fit_hill(c, y)
    assert f.ok and f.p("n") == pytest.approx(1.6, rel=0.15) and f.p("K") == pytest.approx(5.0, rel=0.1)


def test_model_selection_prefers_the_right_family():
    c = np.array([1, 2, 5, 10, 20, 50, 100, 200.0])
    sat = 80.0 * c / (25.0 + c)
    assert cal.best_model(cal.fit_all(c, sat)) in (cal.MICHAELIS, cal.HILL)
    lin = 3.0 * c + 5.0 + RNG.normal(0, 0.5, c.size)
    assert cal.best_model(cal.fit_all(c, lin)) == cal.LINEAR
    logc = np.logspace(-6, -2, 9)
    assert cal.best_model(cal.fit_all(logc, 10 + 59.0 * np.log10(logc))) == cal.LOGLIN


def test_models_fail_gracefully_not_by_raising():
    assert not cal.fit_linear([1], [1]).ok and not cal.fit_loglinear([-1, -2, -3], [1, 2, 3]).ok
    assert not cal.fit_michaelis([1, 2], [1, 2]).ok and not cal.fit_hill([1, 2, 3], [1, 2, 3]).ok
    assert not cal.fit_linear([1, 1, 1], [1, 2, 3]).ok
    assert math.isnan(cal.fit_linear([np.nan, 1, 2], [1, np.nan, 3]).r2) or cal.fit_linear([np.nan, 1, 2], [1, np.nan, 3]).ok is False


def test_linear_range_excludes_the_saturating_end():
    c = np.array([1, 2, 5, 10, 20, 50, 100, 200, 500.0])
    y = np.where(c <= 50, 2.0 * c, 100.0 + 0.2 * (c - 50))            # linear to 50, then nearly flat
    lo, hi, n = cal.linear_range(c, y)
    assert lo == 1 and hi == 50 and n == 6
    assert cal.linear_range(c, 2.0 * c)[:2] == (1.0, 500.0)               # fully linear -> everything
    assert cal.linear_range([1, 2], [1, 2]) [2] == 0                      # too few levels


def test_figures_of_merit_known_answer():
    c = np.array([0, 1, 2, 3, 4, 5.0])
    y = 2.0 * c + 1.0
    blank = [0.9, 1.1, 1.0, 1.05, 0.95]
    f = cal.figures_of_merit(c, y, blank=blank, restrict_to_range=False)
    sd = np.std(blank, ddof=1)
    assert f.sensitivity == pytest.approx(2.0) and f.intercept == pytest.approx(1.0) and f.r2 == pytest.approx(1.0)
    assert f.sigma == pytest.approx(sd) and f.lod == pytest.approx(3 * sd / 2) and f.loq == pytest.approx(10 * sd / 2)
    assert "blank" in f.sigma_source


def test_figures_of_merit_uses_residuals_without_a_blank_and_warns_with_two_points():
    c = np.array([1, 2, 3, 4, 5, 6.0])
    y = 2.0 * c + np.array([0.1, -0.1, 0.1, -0.1, 0.1, -0.1])
    f = cal.figures_of_merit(c, y, restrict_to_range=False)
    assert f.sigma_source == "fit residuals" and f.lod == pytest.approx(3 * f.sigma / f.sensitivity)
    two = cal.figures_of_merit([1, 2], [2, 4])
    assert math.isnan(two.lod) and two.warnings
    assert cal.figures_of_merit([3, 3, 3], [1, 2, 3]).warnings                 # one concentration only


def test_prediction_round_trips_and_has_the_textbook_uncertainty():
    c = np.array([1, 2, 3, 4, 5, 6.0])
    y = 2.0 * c + 1.0 + np.array([0.1, -0.05, 0.08, -0.1, 0.02, -0.03])
    f = cal.fit_linear(c, y)
    x0, se = cal.predict_concentration(f, 9.0)
    a, b = f.p("intercept"), f.p("slope")
    assert x0 == pytest.approx((9.0 - a) / b)
    s = math.sqrt(np.sum(f.residuals ** 2) / 4)
    expect = s / abs(b) * math.sqrt(1 + 1 / 6 + (9.0 - y.mean()) ** 2 / (b ** 2 * np.sum((c - c.mean()) ** 2)))
    assert se == pytest.approx(expect)
    mm = cal.fit_michaelis(np.array([1, 2, 5, 10, 20, 50, 100.0]), 80 * np.array([1, 2, 5, 10, 20, 50, 100.0]) / (25 + np.array([1, 2, 5, 10, 20, 50, 100.0])))
    xr, _ = cal.predict_concentration(mm, float(mm.predict(30.0)))
    assert xr == pytest.approx(30.0, rel=1e-3)
    assert math.isnan(cal.predict_concentration(mm, 90.0)[0])                  # beyond the plateau: no answer
    assert all(math.isnan(v) for v in cal.predict_concentration(f, float("nan")))


def test_replicate_summary():
    out = cal.replicate_summary([1, 1, 1, 2, 2], [10, 11, 12, 20, 22])
    assert out[0][0] == 1 and out[0][1] == pytest.approx(11) and out[0][2] == pytest.approx(1.0) and out[0][3] == 3
    assert out[1][3] == 2 and cal.replicate_summary([1], [5])[0][2] != cal.replicate_summary([1], [5])[0][2]   # sd NaN for n=1


# ===================================================================== peaks (DPV / SWV / LSV)

def gauss(e, e0, sigma, h):
    return h * np.exp(-0.5 * ((e - e0) / sigma) ** 2)


def test_peak_on_a_sloping_baseline_is_recovered():
    e = np.linspace(-0.2, 0.6, 400)
    sigma, h = 0.03, 5.0
    i = gauss(e, 0.2, sigma, h) + 8.0 * e + 1.0                       # baseline 8·E + 1
    r = peaks.analyze_peak(e, i, peaks.PeakParams(window=(-0.1, 0.5)))
    assert r.found and r.e_peak == pytest.approx(0.2, abs=0.004)
    assert r.height == pytest.approx(h, rel=0.03)
    assert r.fwhm == pytest.approx(2.3548 * sigma, rel=0.05)
    assert r.area == pytest.approx(h * sigma * math.sqrt(2 * math.pi), rel=0.05)
    assert r.height_raw > r.height                                    # the raw current sits on the ramp


def test_cathodic_peak_and_polarity():
    e = np.linspace(-0.2, 0.6, 400)
    i = -gauss(e, 0.1, 0.03, 4.0) + 0.5
    r = peaks.analyze_peak(e, i, peaks.PeakParams(window=(-0.1, 0.3)))
    assert r.height < 0 and "cathodic" in r.polarity and r.e_peak == pytest.approx(0.1, abs=0.004)
    up = peaks.analyze_peak(e, i + gauss(e, 0.4, 0.02, 1.0), peaks.PeakParams(window=(0.3, 0.55), polarity="up"))
    assert up.height > 0 and up.e_peak == pytest.approx(0.4, abs=0.004)


def test_window_selects_between_two_peaks_and_polynomial_baseline_handles_curvature():
    e = np.linspace(-0.2, 0.8, 500)
    i = gauss(e, 0.1, 0.03, 9.0) + gauss(e, 0.5, 0.03, 3.0)
    assert peaks.analyze_peak(e, i, peaks.PeakParams(window=(0.35, 0.7))).e_peak == pytest.approx(0.5, abs=0.004)
    curved = gauss(e, 0.3, 0.03, 4.0) + 40.0 * (e - 0.3) ** 2 + 2.0
    lin = peaks.analyze_peak(e, curved, peaks.PeakParams(window=(0.0, 0.6), baseline="linear"))
    poly = peaks.analyze_peak(e, curved, peaks.PeakParams(window=(0.0, 0.6), baseline="poly", poly_order=2))
    assert abs(poly.height - 4.0) < abs(lin.height - 4.0) and poly.height == pytest.approx(4.0, rel=0.1)


def test_noise_and_smoothing_and_edge_warnings():
    e = np.linspace(0, 0.6, 300)
    i = gauss(e, 0.3, 0.03, 5.0) + RNG.normal(0, 0.15, e.size)
    r = peaks.analyze_peak(e, i, peaks.PeakParams(smooth=15))
    assert r.height == pytest.approx(5.0, rel=0.1) and r.e_peak == pytest.approx(0.3, abs=0.01)
    edge = peaks.analyze_peak(e, gauss(e, 0.58, 0.05, 5.0), peaks.PeakParams(window=(0.1, 0.6), baseline="none"))
    assert any("edge" in w for w in edge.warnings)                                # raw maximum 4 % from the window end
    centred = peaks.analyze_peak(e, gauss(e, 0.35, 0.03, 5.0), peaks.PeakParams(window=(0.1, 0.6), baseline="none"))
    assert not any("edge" in w for w in centred.warnings)
    assert not peaks.analyze_peak([0, 1], [0, 1]).found                       # too few points


def test_sweep_features_of_a_sigmoid():
    e = np.linspace(-0.4, 0.4, 400)
    i = 12.0 / (1 + np.exp(-(e - 0.05) / 0.02)) + 0.3
    s = peaks.sweep_features(e, i)
    assert s.limiting == pytest.approx(12.0, rel=0.02)
    assert s.half_wave == pytest.approx(0.05, abs=0.004)
    assert -0.05 < s.onset < 0.05                                            # tangent meets the baseline just before E½


# ===================================================================== EIS

F = np.logspace(5, -1, 60)


def noisy(z, rel=0.005):
    return z * (1 + RNG.normal(0, rel, z.size)) + 1j * 0


def test_randles_cpe_is_recovered():
    true = dict(Rs=100.0, Rct=1000.0, Q=2e-6, n=0.9)
    z = noisy(eis.impedance("randles", true, F))
    fit = eis.fit_circuit(F, z.real, z.imag, "randles")
    assert fit.converged and fit.rmse_rel < 0.02
    assert fit.params["Rs"] == pytest.approx(100.0, rel=0.03) and fit.params["Rct"] == pytest.approx(1000.0, rel=0.03)
    assert fit.params["n"] == pytest.approx(0.9, abs=0.03) and fit.params["Q"] == pytest.approx(2e-6, rel=0.2)
    assert all(v > 0 for v in fit.se.values())
    assert fit.c_eff == pytest.approx((true["Q"] * true["Rct"] ** (1 - true["n"])) ** (1 / true["n"]), rel=0.2)


def test_randles_with_warburg_and_the_simple_rc():
    true = dict(Rs=50.0, Rct=500.0, Q=5e-6, n=0.92, sigma=150.0)
    z = eis.impedance("randles_w", true, F)
    fit = eis.fit_circuit(F, z.real, z.imag, "randles_w")
    assert fit.params["Rct"] == pytest.approx(500.0, rel=0.05) and fit.params["sigma"] == pytest.approx(150.0, rel=0.1)
    rc = eis.fit_circuit(F, *(lambda zz: (zz.real, zz.imag))(eis.impedance("rc", dict(Rs=20.0, Rct=300.0, C=1e-6), F)), "rc")
    assert rc.params["Rs"] == pytest.approx(20.0, rel=1e-3) and rc.params["Rct"] == pytest.approx(300.0, rel=1e-3)
    assert rc.params["C"] == pytest.approx(1e-6, rel=1e-3) and rc.rmse_rel < 1e-6


def test_wrong_circuit_is_flagged_and_frequency_range_is_honoured():
    z = eis.impedance("randles_w", dict(Rs=50.0, Rct=500.0, Q=5e-6, n=0.9, sigma=400.0), F)
    bad = eis.fit_circuit(F, z.real, z.imag, "rc")
    assert bad.rmse_rel > 0.05 and any("Poor fit" in w for w in bad.warnings)
    sub = eis.fit_circuit(F, z.real, z.imag, "randles", fmin=10.0)
    assert sub.f.min() >= 10.0 and len(sub.f) < len(F)
    assert eis.fit_circuit(F[:3], z.real[:3], z.imag[:3], "randles").warnings                # too few points
    with pytest.raises(ValueError):
        eis.fit_circuit(F, z.real, z.imag, "nonsense")


def test_impedance_limits():
    p = dict(Rs=10.0, Rct=200.0, Q=1e-5, n=1.0)
    hi, lo = eis.impedance("randles", p, np.array([1e9])), eis.impedance("randles", p, np.array([1e-6]))
    assert hi[0].real == pytest.approx(10.0, rel=1e-3) and lo[0].real == pytest.approx(210.0, rel=1e-3)
    assert eis.impedance("randles", p, np.array([100.0]))[0].imag < 0                          # capacitive


def test_k0_from_rct_and_bode():
    k = eis.k0_from_rct(rct=1000.0, area_cm2=0.07, conc_mM=5.0, n=1)
    direct = 8.314462618 * 298.15 / (96485.33212 ** 2 * 0.07 * 5e-6 * 1000.0)
    assert k == pytest.approx(direct) and 1e-5 < k < 1e-1
    assert eis.k0_from_rct(2000.0, 0.07, 5.0) == pytest.approx(k / 2)
    assert math.isnan(eis.k0_from_rct(0.0, 0.07, 5.0))
    mag, ph = eis.bode(F, eis.impedance("randles", dict(Rs=10, Rct=200, Q=1e-5, n=1.0), F))
    assert mag.shape == F.shape and 0 < ph.max() < 90


# ===================================================================== electrode characterisation

def test_randles_sevcik_matches_the_textbook_constant():
    """ip = 2.69e5 · n^1.5 · A · D^0.5 · C · ν^0.5 (A, cm², cm²/s, mol/cm³ at 25 °C)."""
    slope = electrode.randles_sevcik_slope(n=1, area_cm2=0.07, conc_mM=5.0, d_cm2s=7.6e-6)
    textbook = 2.69e5 * 0.07 * math.sqrt(7.6e-6) * 5e-6 * 1e6
    assert slope == pytest.approx(textbook, rel=0.01) and 255 < slope < 265
    assert electrode.ecsa_from_slope(slope, 1, 5.0, 7.6e-6) == pytest.approx(0.07)
    assert electrode.diffusion_from_slope(slope, 1, 0.07, 5.0) == pytest.approx(7.6e-6)
    assert electrode.randles_sevcik_slope(2, 0.07, 5.0, 7.6e-6) == pytest.approx(slope * 2 ** 1.5, rel=1e-6)


def test_scan_rate_analysis_diffusion_control_and_ecsa():
    nu = np.array([0.01, 0.025, 0.05, 0.1, 0.2, 0.4])
    slope = electrode.randles_sevcik_slope(1, 0.05, 5.0, 7.6e-6)              # true electroactive area 0.05 cm²
    r = electrode.scan_rate_analysis(nu, slope * np.sqrt(nu), -slope * np.sqrt(nu), 0.30 + 0 * nu, 0.24 + 0 * nu,
                                     n=1, conc_mM=5.0, d_cm2s=7.6e-6, area_geo_cm2=0.0314)
    assert r.fit_a.r2 > 0.9999 and r.loglog_a == pytest.approx(0.5, abs=0.01) and r.regime == "diffusion-controlled"
    assert r.ecsa_a == pytest.approx(0.05, rel=0.01) and r.ecsa_c == pytest.approx(0.05, rel=0.01)
    assert r.roughness == pytest.approx(0.05 / 0.0314, rel=0.02)


def test_scan_rate_analysis_recognises_adsorption_and_warns_when_not_linear():
    nu = np.array([0.01, 0.025, 0.05, 0.1, 0.2, 0.4])
    ads = electrode.scan_rate_analysis(nu, 40 * nu, -40 * nu, 0.3 + 0 * nu, 0.25 + 0 * nu)
    assert ads.loglog_a == pytest.approx(1.0, abs=0.01) and "adsorption" in ads.regime
    assert any("not linear" in w for w in ads.warnings)                     # ip is linear in ν, not in √ν
    assert electrode.scan_rate_analysis([0.1, 0.2], [1, 2], [1, 2], [0, 0], [0, 0]).warnings


def test_laviron_recovers_alpha_n_and_ks():
    alpha, n, ks, e0 = 0.4, 1.0, 1.0, 0.1
    rtf = 8.314462618 * 298.15 / 96485.33212
    nu = np.array([2.0, 5.0, 10.0, 20.0, 50.0, 100.0])
    dep = (alpha * math.log10(1 - alpha) + (1 - alpha) * math.log10(alpha) - np.log10(rtf / (n * nu)) - math.log10(ks)) \
        * math.log(10) * rtf / (alpha * (1 - alpha) * n)
    assert dep.min() > 0.2                                                    # all in the Laviron (ΔEp > 200/n mV) regime
    r = electrode.laviron(nu, e0 + alpha * dep, e0 - (1 - alpha) * dep, n_guess=1)
    assert r.alpha == pytest.approx(alpha, rel=1e-6) and r.n == pytest.approx(n, rel=1e-6)
    assert r.ks == pytest.approx(ks, rel=1e-6) and r.n_points == 6
    assert math.isnan(electrode.laviron([0.01, 0.02, 0.05], [0.3] * 3, [0.29] * 3).alpha)       # reversible: not applicable


def test_double_layer_capacitance():
    nu = np.array([0.01, 0.02, 0.05, 0.1, 0.2])
    out = electrode.double_layer_capacitance(nu, 25.0 * nu, area_cm2=0.07, cs_uf_cm2=20.0)
    assert out["cdl_uF"] == pytest.approx(25.0) and out["per_area"] == pytest.approx(25.0 / 0.07)
    assert out["roughness"] == pytest.approx(25.0 / 0.07 / 20.0) and out["r2"] == pytest.approx(1.0)


# ===================================================================== transients

def test_response_time_of_a_first_order_step():
    tau = 2.0
    t = np.arange(0, 40, 0.01)
    y = np.where(t < 10, 1.0, 1.0 + 4.0 * (1 - np.exp(-(t - 10) / tau)))
    r = transients.response_time(t, y, t_step=10.0, pre=5.0, post_window=5.0)
    assert r.t_low == pytest.approx(-tau * math.log(0.9), rel=0.04, abs=0.01)
    assert r.t_high == pytest.approx(-tau * math.log(0.1), rel=0.04)
    assert r.rise == pytest.approx(tau * (math.log(10) - math.log(10 / 9)), rel=0.04)
    assert r.y_start == pytest.approx(1.0) and r.y_end == pytest.approx(5.0, rel=1e-3)
    flat = transients.response_time(t, np.ones_like(t), 10.0)
    assert flat.warnings and math.isnan(flat.t_high)
    assert transients.response_time(t, y, 0.0).warnings                       # nothing recorded before the step


def test_steady_state_separates_noise_from_drift():
    t = np.arange(0, 60, 0.1)
    y = 5.0 + 0.002 * t + RNG.normal(0, 0.01, t.size)
    s = transients.steady_state(t, y, 10, 60)
    assert s.level == pytest.approx(5.0 + 0.002 * 35, abs=0.01)
    assert s.drift == pytest.approx(0.002, rel=0.15) and s.sd == pytest.approx(0.01, rel=0.2)
    assert s.drift_rel_pct_per_min == pytest.approx(0.002 * 60 / 5.07 * 100, rel=0.2)
    assert s.snr > 300 and transients.steady_state(t, y, 100, 200).n == 0


def test_cottrell_recovers_the_diffusion_coefficient():
    k = 96485.33212 * 0.07 * 5e-6 * math.sqrt(7.6e-6 / math.pi) * 1e6          # µA·s^½
    t = np.arange(0.1, 10, 0.05)
    r = transients.cottrell(t, k / np.sqrt(t), 0.5, 10, n_e=1, area_cm2=0.07, conc_mM=5.0)
    assert r.slope == pytest.approx(k, rel=1e-6) and r.d_cm2s == pytest.approx(7.6e-6, rel=1e-4) and r.r2 > 0.99999
    not_diffusive = transients.cottrell(t, 5 + 0 * t + RNG.normal(0, 1, t.size), 0.5, 10)
    assert not_diffusive.warnings


def test_anson_recovers_d_and_the_adsorbed_charge():
    k = 96485.33212 * 0.07 * 5e-6 * math.sqrt(7.6e-6 / math.pi) * 1e6
    t = np.arange(0.05, 8, 0.05)
    q = 2 * k * np.sqrt(t) + 3.0                                               # 3 µC intercept
    r = transients.anson(t, q, 0.1, 8, n_e=1, area_cm2=0.07, conc_mM=5.0, q_dl_uc=1.0)
    assert r.slope == pytest.approx(2 * k, rel=1e-6) and r.intercept == pytest.approx(3.0, rel=1e-6)
    assert r.d_cm2s == pytest.approx(7.6e-6, rel=1e-4)
    assert r.gamma_mol_cm2 == pytest.approx(2.0e-6 / (96485.33212 * 0.07), rel=1e-6)


# ===================================================================== study statistics

def test_group_stats_known_values():
    g = studies.group_stats([10, 12, 14, 16])
    assert g.n == 4 and g.mean == 13 and g.sd == pytest.approx(math.sqrt(20 / 3))
    assert g.rsd_pct == pytest.approx(g.sd / 13 * 100) and g.sem == pytest.approx(g.sd / 2)
    assert g.ci95 == pytest.approx(stats.t.ppf(0.975, 3) * g.sem)
    one = studies.group_stats([5.0])
    assert one.n == 1 and one.mean == 5 and math.isnan(one.sd)
    assert studies.group_stats([]).n == 0 and studies.group_stats([None, float("nan")]).n == 0


def test_grubbs_flags_a_real_outlier_and_only_that():
    assert studies.grubbs_outliers([10.0, 10.1, 9.9, 10.05, 9.95, 10.1, 14.0]) == [6]
    assert studies.grubbs_outliers([10.0, 10.1, 9.9, 10.05, 9.95, 10.1]) == []
    assert studies.grubbs_outliers([1, 2]) == []                              # too few


def test_stability_exponential_decay_and_linear_drift():
    t = np.array([0, 2, 5, 10, 15, 20, 30.0])
    s = studies.stability(t, 100 * np.exp(-t / 20.0))
    assert s.tau == pytest.approx(20.0, rel=1e-3) and s.half_life == pytest.approx(20 * math.log(2), rel=1e-3)
    assert s.final_retention_pct == pytest.approx(100 * math.exp(-1.5)) and s.retention_pct[0] == 100
    lin = studies.stability([0, 10, 20, 30], [100, 95, 90, 85])
    assert lin.drift_per_unit == pytest.approx(-0.5) and lin.drift_pct_per_unit == pytest.approx(-0.5)
    assert lin.t_90 == pytest.approx(20.0)
    assert studies.stability([1], [1]).warnings and studies.stability([0, 1], [0, 1]).warnings


def test_selectivity_and_recovery_numbers():
    rows = studies.selectivity(10.0, 100.0, [("ascorbate", 1.0, 100.0), ("urea", 0.5, 200.0)])
    assert rows[0].interference_pct == pytest.approx(10.0) and rows[0].k_sel == pytest.approx(0.1)
    assert rows[1].k_sel == pytest.approx((0.5 / 200) / (10 / 100)) and math.isnan(rows[1].interference_pct)
    assert rows[0].change_pct == pytest.approx(-90.0)
    rec = studies.recovery([("s1", 10.0, 9.5), ("s2", 10.0, 10.5), ("s3", 20.0, 19.0)])
    assert [round(r.recovery_pct, 2) for r in rec.rows] == [95.0, 105.0, 95.0]
    assert rec.mean_pct == pytest.approx(98.333, abs=1e-3) and rec.rsd_pct == pytest.approx(rec.sd_pct / rec.mean_pct * 100)
