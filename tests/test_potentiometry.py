"""Ion-selective electrodes: data generated from the Nikolsky–Eisenman equation, so the right answers are known."""
import math

import numpy as np
import pytest

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
