"""Bench calculators checked against values worked out by hand."""
import math

import pytest

from echem_analyzer import bench
from echem_analyzer.bench import BenchError
from echem_analyzer.ca_analysis import added_concentrations


# ===================================================================== dilutions

def test_each_unknown_of_c1v1_equals_c2v2_can_be_solved():
    d = bench.solve_dilution(c1=10, c2=0.1, v2=50)                       # 10 mM -> 0.1 mM, 50 mL
    assert d.v1 == pytest.approx(0.5) and d.diluent == pytest.approx(49.5) and d.factor == pytest.approx(100)
    assert bench.solve_dilution(c1=10, v1=0.5, v2=50).c2 == pytest.approx(0.1)
    assert bench.solve_dilution(v1=0.5, c2=0.1, v2=50).c1 == pytest.approx(10)
    assert bench.solve_dilution(c1=10, v1=0.5, c2=0.1).v2 == pytest.approx(50)


@pytest.mark.parametrize("kwargs,words", [
    (dict(c1=10, v1=1, c2=1, v2=5), "exactly one"),                       # nothing to solve
    (dict(c1=10, v1=1), "exactly one"),
    (dict(c1=10, c2=20, v2=5), "not a dilution"),                         # cannot dilute upwards
    (dict(c1=10, c2=1, v2=-5), "positive"),
    (dict(c1=0, c2=1, v2=5), "positive"),
])
def test_impossible_dilutions_explain_themselves(kwargs, words):
    with pytest.raises(BenchError, match=words):
        bench.solve_dilution(**kwargs)


def test_serial_dilution_tubes():
    rows = bench.serial_dilution(1.0, 10, 4, 1000)                         # 1 mM stock, tenfold, 1 mL tubes
    assert [r.conc for r in rows] == pytest.approx([0.1, 0.01, 0.001, 0.0001])
    assert all(r.take == pytest.approx(100) and r.diluent == pytest.approx(900) for r in rows)
    with pytest.raises(BenchError):
        bench.serial_dilution(1.0, 1, 4, 1000)
    with pytest.raises(BenchError):
        bench.serial_dilution(1.0, 10, 0, 1000)


# ===================================================================== standard addition

def test_addition_plan_matches_the_worked_example_and_the_apps_own_model():
    plan = bench.addition_plan(stock=10_000, v0_ml=10, targets=[10, 20, 50, 100])        # 10 mM stock (in µM), 10 mL cell
    assert plan.steps[0].add_ul == pytest.approx(10 * 10_000 / (10_000 - 10) * 1.0, rel=1e-9)  # C·V0/(cs−C) in µL = 10.01
    assert plan.steps[0].add_ul == pytest.approx(10.01, abs=0.001)
    volumes = [s.add_ul for s in plan.steps]
    back = added_concentrations(10_000, 10, volumes)[1:]                                 # fed to the standard-addition analysis ...
    assert back == pytest.approx([10, 20, 50, 100])                                      # ... it gives the targets again
    assert plan.steps[-1].cumulative_ul == pytest.approx(sum(volumes)) and plan.total_ul == pytest.approx(sum(volumes))
    assert plan.steps[-1].matrix_pct == pytest.approx(100 * 10_000 / (10_000 + plan.total_ul))
    assert plan.warnings == []


def test_rounding_to_what_a_pipette_can_set_changes_the_concentration_actually_reached():
    plan = bench.addition_plan(10_000, 10, [10, 20, 50, 100], round_to_ul=0.5)
    assert all(s.add_ul % 0.5 == pytest.approx(0, abs=1e-9) or s.add_ul % 0.5 == pytest.approx(0.5) for s in plan.steps)
    back = added_concentrations(10_000, 10, [s.add_ul for s in plan.steps])[1:]
    assert [s.actual for s in plan.steps] == pytest.approx(list(back))
    assert any(abs(s.actual - s.target) > 1e-6 for s in plan.steps)                      # rounding is not free


def test_addition_plan_warns_about_tiny_volumes_and_a_diluted_matrix():
    tiny = bench.addition_plan(100_000, 10, [1, 5])                                       # 100 mM stock for a 1 µM step: 0.1 µL
    assert any("below the 0.5 µL" in w for w in tiny.warnings)
    big = bench.addition_plan(1000, 10, [100, 300])                                       # 1 mM stock for 300 µM: lots of volume
    assert any("total" in w and "of the cell volume" in w for w in big.warnings)
    need = bench.min_stock_for(300, 5)
    assert need == pytest.approx(300 * 1.05 / 0.05)
    assert bench.addition_plan(need, 10, [100, 300]).total_pct_of_cell == pytest.approx(5.0)   # exactly at the limit


@pytest.mark.parametrize("targets,words", [([], "at least one"), ([5, 5], "increase"), ([5, 2], "increase"), ([0, 1], "positive"),
                                           ([10, 2000], "below the stock")])
def test_addition_plan_rejects_impossible_targets(targets, words):
    with pytest.raises(BenchError, match=words):
        bench.addition_plan(1000, 10, targets)


# ===================================================================== what to expect

def test_ferricyanide_peak_current_is_the_textbook_value():
    # 1 mM Fe(CN)6^3-, 0.07 cm2 electrode, D = 7.6e-6 cm2/s, 100 mV/s, 25 C: ip = 2.69e5 n^1.5 A D^0.5 C v^0.5 = 16.4 uA
    ip = bench.expected_peak_current(1, 0.07, 1.0, 7.6e-6, 0.1)
    assert ip == pytest.approx(2.69e5 * 0.07 * math.sqrt(7.6e-6) * 1e-6 * math.sqrt(0.1) * 1e6, rel=0.005)
    assert ip == pytest.approx(16.4, abs=0.1)
    assert bench.expected_peak_current(1, 0.07, 1.0, 7.6e-6, 0.4) == pytest.approx(2 * ip)               # grows with sqrt(scan rate)
    assert bench.expected_peak_current(1, 0.07, 1.0, 7.6e-6, 0.1, alpha_n=0.5) < ip                        # irreversible is smaller
    with pytest.raises(BenchError):
        bench.expected_peak_current(0, 0.07, 1.0, 7.6e-6, 0.1)


def test_current_range_is_the_smallest_decade_with_headroom():
    advice = bench.current_range(16.4)                                  # x1.5 = 24.6 uA -> the 100 uA range
    assert advice.range_ua == 100.0 and advice.label == "100 µA" and advice.fill_pct == pytest.approx(16.4)
    assert bench.current_range(0.4).label == "1 µA" and bench.current_range(0.0004).label == "1 nA"
    assert bench.current_range(2e5).range_ua is None
    assert bench.current_range(-16.4).range_ua == 100.0                 # a cathodic current is as big as an anodic one
    assert [bench.current_range_label(r) for r in bench.CURRENT_RANGES_UA] == \
        ["1 nA", "10 nA", "100 nA", "1 µA", "10 µA", "100 µA", "1 mA", "10 mA", "100 mA"]


# ===================================================================== potentiometry

def test_nernstian_slope_and_its_temperature_dependence():
    cation = bench.nernst_prediction(1, 25.0, 1e-4, 1e-2)
    assert cation.slope_mv_per_decade == pytest.approx(59.16, abs=0.01) and cation.decades == pytest.approx(2.0)
    assert cation.delta_mv == pytest.approx(118.3, abs=0.05)
    assert cation.slope_change_per_kelvin == pytest.approx(0.1984, abs=0.001)                            # 59.16 / 298.15
    assert bench.nernst_prediction(2, 25.0, 1e-4, 1e-3).slope_mv_per_decade == pytest.approx(29.58, abs=0.01)
    assert bench.nernst_prediction(-1, 25.0, 1e-4, 1e-3).slope_mv_per_decade == pytest.approx(-59.16, abs=0.01)   # anions fall
    assert bench.nernst_prediction(1, 37.0, 1e-4, 1e-3).slope_mv_per_decade == pytest.approx(61.54, abs=0.01)    # body temperature
    for bad in (0, 1.5):
        with pytest.raises(BenchError):
            bench.nernst_prediction(bad, 25.0, 1e-4, 1e-3)


# ===================================================================== ionic strength

@pytest.mark.parametrize("conc,zc,za,expected", [(0.1, 1, 1, 0.1), (0.1, 1, 2, 0.3), (0.1, 2, 2, 0.4), (0.1, 2, 1, 0.3), (0.05, 3, 1, 0.30)])               # AlCl3: Al3+ 0.05 M and Cl- 0.15 M, I = (0.05*9 + 0.15*1)/2
def test_ionic_strength_of_common_salts(conc, zc, za, expected):
    assert bench.ionic_strength(bench.salt_species(conc, zc, za)) == pytest.approx(expected)


def test_ionic_strength_of_a_mixture_and_the_debye_length():
    pbs = [(0.137, 1), (0.137, -1), (0.0027, 1), (0.0027, -1), (0.0107, 1), (0.0107, -1)]               # roughly 1x PBS salts
    assert bench.ionic_strength(pbs) == pytest.approx(0.150, abs=0.001)
    assert bench.debye_length_nm(0.1) == pytest.approx(0.961, rel=0.01)                                  # textbook: 0.304/sqrt(I) nm
    assert bench.debye_length_nm(0.001) == pytest.approx(9.61, rel=0.01)
    assert bench.debye_length_nm(0.15) < bench.debye_length_nm(0.015) < bench.debye_length_nm(0.0015)    # a diluted sample screens less
    assert bench.relative_permittivity_water(25) == pytest.approx(78.3, abs=0.1)
    with pytest.raises(BenchError):
        bench.debye_length_nm(0)
    with pytest.raises(BenchError):
        bench.salt_species(0.1, 0, 1)


# ===================================================================== weighing

def test_mass_to_weigh():
    # 10 mL of 50 mM K3Fe(CN)6 (329.24 g/mol): 0.05 mol/L x 0.010 L x 329.24 g/mol = 164.6 mg
    assert bench.mass_to_weigh_mg(50, "mM", 10, 329.24) == pytest.approx(164.62)
    assert bench.mass_to_weigh_mg(1, "M", 1, 58.44) == pytest.approx(58.44)                       # 1 mol/L x 1 mL x 58.44 g/mol
    assert bench.mass_to_weigh_mg(500, "µM", 100, 180.16) == pytest.approx(9.008)
    with pytest.raises(BenchError):
        bench.mass_to_weigh_mg(1, "mg/L", 1, 10)
