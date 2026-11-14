"""Calibration diagnostics and the inverse-prediction interval, checked against brute force and by simulation."""
import math

import numpy as np
import pytest
from scipy import stats

from echem_analyzer import calibration as cal


def line(x, a=0.5, b=2.0, sigma=0.3, seed=1):
    x = np.asarray(x, float)
    return x, a + b * x + np.random.default_rng(seed).normal(0, sigma, x.size)


# ===================================================================== diagnostics

def test_leverage_of_equally_spaced_standards_is_the_textbook_value():
    x = np.array([1, 2, 3, 4, 5.0])
    d = cal.diagnostics(cal.fit_linear(x, 2 * x + np.array([0.1, -0.1, 0.05, -0.05, 0.02])))
    assert d.leverage == pytest.approx([0.6, 0.3, 0.2, 0.3, 0.6])                    # 1/n + (x − x̄)²/Sxx
    assert d.leverage.sum() == pytest.approx(2.0) and d.dof == 3


def test_studentized_residuals_and_cooks_distance_match_deleting_each_point_and_refitting():
    x, y = line([1, 2, 3, 4, 5, 6, 8, 10.0], sigma=0.4, seed=4)
    y[2] += 1.2                                                                        # something to find
    fit = cal.fit_linear(x, y)
    d = cal.diagnostics(fit)
    n, p = len(x), 2
    s2 = np.sum(fit.residuals ** 2) / (n - p)
    for i in range(n):
        keep = np.arange(n) != i
        f_i = cal.fit_linear(x[keep], y[keep])
        s2_i = np.sum(f_i.residuals ** 2) / (n - 1 - p)
        design_i = np.column_stack([np.ones(n - 1), x[keep]])
        xi = np.array([1.0, x[i]])
        pred_var = s2_i * (1 + xi @ np.linalg.inv(design_i.T @ design_i) @ xi)
        assert d.studentized[i] == pytest.approx((y[i] - f_i.predict(x[i])) / math.sqrt(pred_var), rel=1e-9)
        assert d.cooks[i] == pytest.approx(np.sum((fit.predict(x) - f_i.predict(x)) ** 2) / (p * s2), rel=1e-9)


def test_an_injected_outlier_is_found_and_clean_data_is_left_alone():
    x, y = line(np.repeat([1, 2, 5, 10, 20, 50.0], 2), sigma=0.2, seed=7)
    assert cal.diagnostics(cal.fit_linear(x, y)).flagged == []
    y_bad = y.copy()
    y_bad[5] += 8.0                                                                    # 40 σ off
    d = cal.diagnostics(cal.fit_linear(x, y_bad))
    assert 5 in d.outliers and "far from the line" in d.reason(5)
    assert d.reason(0) == ""


def test_clean_calibrations_are_rarely_flagged_whatever_the_number_of_standards():
    flagged = 0
    for seed in range(400):
        x, y = line(np.linspace(1, 10, 8), sigma=0.5, seed=seed)
        flagged += bool(cal.diagnostics(cal.fit_linear(x, y)).outliers)
    assert flagged / 400 < 0.08                                                        # Bonferroni keeps it near 5 % at most


def test_a_point_that_drags_the_line_is_influential():
    x = np.array([1, 2, 3, 4, 5, 6, 30.0])                                             # one standard far beyond the rest ...
    y = np.array([2.1, 3.9, 6.2, 7.8, 10.1, 12.0, 38.0])                               # ... and well below the line the others make
    d = cal.diagnostics(cal.fit_linear(x, y))
    assert 6 in d.influential and d.leverage[6] > 0.9


def test_perfect_fits_few_points_and_saturating_models_have_no_diagnostics_to_show():
    x = np.array([1, 2, 3, 4.0])
    perfect = cal.diagnostics(cal.fit_linear(x, 3 * x + 1))
    assert perfect.flagged == [] and not perfect.studentized.any()
    assert cal.diagnostics(cal.fit_linear([1, 2, 3.0], [2, 4, 6.5])) is None           # three points: nothing to compare against
    xm = np.array([1, 2, 5, 10, 20, 50.0])
    assert cal.diagnostics(cal.fit_michaelis(xm, 10 * xm / (5 + xm))) is None
    assert cal.diagnostics(cal.fit_linear([1, 1], [1, 1])) is None                     # a failed fit


def test_log_linear_diagnostics_work_in_log_concentration():
    x = np.array([1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1])
    y = 200 + 59.2 * np.log10(x) + np.array([0.3, -0.4, 0.2, 0.1, -0.2, 0.3])
    y_bad = y.copy()
    y_bad[2] += 25
    d = cal.diagnostics(cal.fit_loglinear(x, y_bad))
    assert d.model == cal.LOGLIN and 2 in d.outliers
    np.testing.assert_allclose(d.leverage, cal.diagnostics(cal.fit_linear(np.log10(x), y_bad)).leverage)


# ===================================================================== inverse-prediction interval

def test_the_interval_is_the_line_crossing_the_prediction_band():
    x, y = line(np.repeat([1, 2, 3, 4, 5, 6.0], 2), sigma=0.3, seed=3)
    fit = cal.fit_linear(x, y)
    iv = cal.prediction_interval(fit, 7.0)
    assert iv.ok and iv.lo < iv.x < iv.hi and iv.x == pytest.approx((7.0 - fit.p("intercept")) / fit.p("slope"))
    n, s = len(x), math.sqrt(np.sum(fit.residuals ** 2) / (len(x) - 2))
    sxx, xbar = np.sum((x - x.mean()) ** 2), x.mean()
    t = stats.t.ppf(0.975, n - 2)
    for edge in (iv.lo, iv.hi):                                                        # at each end the response sits on the band
        band = t * s * math.sqrt(1 + 1 / n + (edge - xbar) ** 2 / sxx)
        assert abs(7.0 - fit.predict(edge)) == pytest.approx(band, rel=1e-9)


def test_for_a_tight_calibration_it_agrees_with_the_usual_x_plus_or_minus_t_se():
    x, y = line(np.repeat([1, 2, 3, 4, 5, 6.0], 3), sigma=0.05, seed=9)
    fit = cal.fit_linear(x, y)
    iv = cal.prediction_interval(fit, 7.0)
    x0, se = cal.predict_concentration(fit, 7.0)
    half = stats.t.ppf(0.975, len(x) - 2) * se
    assert (iv.hi - iv.lo) / 2 == pytest.approx(half, rel=0.02)
    wider = cal.prediction_interval(fit, 7.0, level=0.99)
    assert wider.lo < iv.lo and wider.hi > iv.hi                                      # higher confidence, wider interval
    averaged = cal.prediction_interval(fit, 7.0, n_rep=4)
    assert (averaged.hi - averaged.lo) < (iv.hi - iv.lo)                               # four readings pin the response down better


def test_a_95_percent_interval_covers_the_true_concentration_95_percent_of_the_time():
    rng = np.random.default_rng(2024)
    x = np.repeat([1, 2, 3, 4, 5, 6.0], 2)
    covered = 0
    trials = 3000
    for _ in range(trials):
        y = 0.5 + 2.0 * x + rng.normal(0, 0.8, x.size)
        x_true = rng.uniform(1.5, 5.5)
        y0 = 0.5 + 2.0 * x_true + rng.normal(0, 0.8)
        iv = cal.prediction_interval(cal.fit_linear(x, y), y0)
        covered += iv.ok and iv.lo <= x_true <= iv.hi
    assert covered / trials == pytest.approx(0.95, abs=0.02)


def test_no_finite_interval_when_the_slope_cannot_be_told_from_zero():
    x = np.array([1, 2, 3, 4, 5, 6.0])
    y = np.array([5.1, 4.8, 5.3, 4.9, 5.2, 5.0])                                       # a flat response: no sensitivity
    iv = cal.prediction_interval(cal.fit_linear(x, y), 5.0)
    assert not iv.ok and "prediction band" in iv.method


def test_log_linear_intervals_are_positive_and_skewed_the_way_a_logarithm_is():
    x = np.repeat([1e-6, 1e-5, 1e-4, 1e-3, 1e-2], 2)
    y = 200 + 59.2 * np.log10(x) + np.random.default_rng(5).normal(0, 1.0, x.size)
    fit = cal.fit_loglinear(x, y)
    iv = cal.prediction_interval(fit, 200 + 59.2 * np.log10(3e-4))
    assert iv.ok and 0 < iv.lo < iv.x < iv.hi
    assert iv.hi - iv.x > iv.x - iv.lo                                                 # skewed upward on a linear scale ...
    assert math.log(iv.hi / iv.x) == pytest.approx(math.log(iv.x / iv.lo), rel=0.02)   # ... and nearly even in log10


def test_saturating_models_get_an_approximate_interval_and_say_so():
    x = np.array([1, 2, 5, 10, 20, 50, 100.0])
    y = 100 * x / (15 + x) + np.random.default_rng(1).normal(0, 0.8, x.size)
    fit = cal.fit_michaelis(x, y)
    iv = cal.prediction_interval(fit, 50.0)
    assert iv.ok and iv.lo <= iv.x <= iv.hi and "approximate" in iv.method
    assert not cal.prediction_interval(fit, 500.0).ok                                  # above the plateau: cannot be inverted
    assert not cal.prediction_interval(fit, float("nan")).ok
