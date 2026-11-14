"""Import-time checks on curves: they must catch bad data and stay quiet on good data."""
import numpy as np

import synth
from echem_analyzer import dataqc, readers
from echem_analyzer.dataqc import ERROR, INFO, WARNING, Flag
from echem_analyzer.models import Curve, DataFile


def make(kind, x, y, x_unit="s", y_unit="µA", uid="a#0", source="/data/a.csv", **extra) -> Curve:
    return Curve(kind=kind, title="t", x=np.asarray(x, float), y=np.asarray(y, float), x_unit=x_unit, y_unit=y_unit,
                 source=source, uid=uid, extra={k: np.asarray(v, float) for k, v in extra.items()})


def codes(curve) -> list[str]:
    return [f.code for f in dataqc.check_curve(curve)]


def staircase(noise=0.01, seed=2):
    t = np.linspace(0, 200, 801)
    level = 1.0 + 0.5 * np.minimum(np.floor(t / 40), 4)            # five steps; the last one runs to the end
    return t, level + np.random.default_rng(seed).normal(0, noise, t.size)


# ===================================================================== good data is left alone

def test_clean_curves_of_every_kind_raise_no_flag():
    e, i = synth.dpv_trace(5.0)
    cv_e, cv_i = synth.cv_scan(1.0, 3.0)
    t, ca = staircase()
    f, z = synth.randles_spectrum(noise=0.002)
    t_ocp = np.linspace(0, 120, 121)
    clean = [make("DPV", e, i, "V"), make("CV", cv_e, cv_i, "V"), make("CA", t, ca),
             make("EIS", z.real, -z.imag, "Ω", "Ω", freq=f, zre=z.real, zim=z.imag),
             make("OCP", t_ocp, 0.25 + 1e-4 * np.exp(-t_ocp / 10), "s", "V")]
    for c in clean:
        assert codes(c) == [], (c.kind, dataqc.describe(dataqc.check_curve(c)))


def test_a_noisy_plateau_at_the_maximum_and_a_real_step_are_not_flagged():
    t, ca = staircase(noise=0.02)
    assert "clipped" not in codes(make("CA", t, ca)) and "spike" not in codes(make("CA", t, ca))   # last step is the maximum
    quantised = np.round(ca, 1)                                       # a coarse ADC repeats values, but not 8 in a row at the top
    assert "clipped" not in codes(make("CA", t, quantised + np.where(np.arange(t.size) % 3 == 0, 0.1, 0)))


# ===================================================================== things that are wrong

def test_points_that_are_not_numbers_are_counted():
    t, ca = staircase()
    ca[10] = np.nan
    (flag,) = dataqc.check_curve(make("CA", t, ca))
    assert (flag.code, flag.severity) == ("nan", WARNING) and "1 of 801" in flag.message
    ca[:600] = np.nan
    assert dataqc.check_curve(make("CA", t, ca))[0].severity == ERROR                 # most of the trace is missing


def test_time_that_runs_backwards_or_repeats_is_an_error_but_a_potential_axis_may_turn():
    t, ca = staircase()
    t[300] = t[299]                                                    # a repeated timestamp
    flag = dataqc.check_curve(make("CA", t, ca))[0]
    assert (flag.code, flag.severity) == ("time-order", ERROR) and "point 301" in flag.message
    e, i = synth.cv_scan(1.0, 3.0)                                      # a CV's potential goes up and down by design
    assert "time-order" not in codes(make("CV", e, i, "V"))


def test_a_clipped_current_is_reported_only_for_currents():
    t = np.linspace(0, 100, 501)
    sig = 20 * np.sin(t / 8) ** 2
    clipped = np.minimum(sig, 12.0)
    flags = dataqc.check_curve(make("CA", t, clipped))
    assert flags[0].code == "clipped" and "12" in flags[0].message
    assert "clipped" not in codes(make("OCP", t, clipped * 1e-2, "s", "V"))        # a potential has no current range


def test_isolated_spikes_are_found_and_the_trailing_one_is_called_out():
    t, ca = staircase()
    ca[400] += 1.5                                                     # a glitch in the middle of a step
    flag = next(f for f in dataqc.check_curve(make("CA", t, ca)) if f.code == "spike")
    assert "1 isolated spike" in flag.message and "last point" not in flag.message
    ca[-1] += 3.0                                                      # PalmSens' usual end-of-run spike
    flag = next(f for f in dataqc.check_curve(make("CA", t, ca)) if f.code == "spike")
    assert "2 isolated spike" in flag.message and "last point" in flag.message


def test_the_start_of_a_transient_and_a_steady_drift_are_not_spikes():
    t = np.linspace(0, 60, 121)
    cottrell = 0.5 + 40 / np.sqrt(t + 0.05)                            # a charging / diffusion transient: steep at the start
    assert codes(make("CA", t, cottrell)) == []
    assert codes(make("CA", t, 2.0 + 0.01 * t)) == []                  # a ramp up to the last point is a trend
    settle = 0.25 + 1e-4 * np.exp(-np.arange(121.0) / 10)
    assert codes(make("OCP", np.arange(121.0), settle, "s", "V")) == []


def test_a_clipped_trace_is_not_also_reported_as_a_storm_of_spikes():
    t, ca = staircase(noise=0.01)
    clipped = np.minimum(ca, 2.0)                    # two thirds of the trace is now exactly constant; the rest is still noisy
    assert codes(make("CA", t, clipped)) == ["clipped"]


def test_flat_and_very_short_traces():
    t = np.linspace(0, 10, 101)
    assert codes(make("CA", t, np.full_like(t, 0.7))) == ["flat"]
    assert codes(make("CA", t[:3], t[:3])) == ["short"]
    assert codes(make("CA", [0, 1, 2, 3], [1, np.nan, np.nan, np.nan]))[:2] == ["nan", "short"]        # errors before warnings


def test_impedance_needs_positive_frequencies_and_enough_of_them():
    f, z = synth.randles_spectrum()
    bad = f.copy()
    bad[3] = 0.0
    flag = dataqc.check_curve(make("EIS", z.real, -z.imag, "Ω", "Ω", freq=bad, zre=z.real, zim=z.imag))[0]
    assert (flag.code, flag.severity) == ("freq", ERROR)
    few = make("EIS", z.real[:3], -z.imag[:3], "Ω", "Ω", freq=f[:3], zre=z.real[:3], zim=z.imag[:3])
    assert codes(few) == ["short"]
    assert codes(make("EIS", z.real, -z.imag, "Ω", "Ω")) == ["freq"]                  # no frequency column at all


def test_an_open_circuit_potential_that_has_not_settled_is_flagged_but_a_short_or_settled_one_is_not():
    t = np.linspace(0, 120, 121)
    drifting = make("OCP", t, 0.25 + 3e-3 / 60 * t, "s", "V")                          # +3 mV/min
    flag = dataqc.check_curve(drifting)[0]
    assert flag.code == "drift" and "+3.0 mV/min" in flag.message
    assert codes(make("OCP", t, 0.25 + 0.1e-3 / 60 * t, "s", "V")) == []               # 0.1 mV/min
    assert codes(make("OCP", t[:15], 0.25 + 3e-3 / 60 * t[:15], "s", "V")) == []        # too short to say
    assert codes(make("CP", t, 0.25 + 3e-3 / 60 * t, "s", "V")) == []                  # a chronopotentiogram is meant to move


def test_units_that_cannot_be_right_are_questioned():
    e, i = synth.dpv_trace(5.0)
    assert codes(make("DPV", e * 1000, i, "V")) == ["units"]                            # millivolts labelled as volts
    assert codes(make("DPV", e, i * 1e5, "V")) == ["units"]                             # nanoamps labelled as microamps
    assert codes(make("OCP", np.arange(60.0), np.full(60, 0.2) + np.arange(60) * 1e-6, "s", "V")) == []


# ===================================================================== across files

def test_the_same_data_loaded_twice_is_flagged_on_the_second_copy_only():
    t, ca = staircase()
    a = DataFile(path="a.csv", fmt="csv", curves=[make("CA", t, ca, uid="a#0", source="/data/a.csv")])
    b = DataFile(path="b.csv", fmt="csv", curves=[make("CA", t, ca + 1e-12, uid="b#0", source="/data/b.csv"),
                                                  make("CA", t, ca * 2, uid="b#1", source="/data/b.csv")])
    flags = dataqc.check_files([a, b])
    assert flags["a#0"] == [] and flags["b#1"] == []
    (dup,) = flags["b#0"]
    assert dup.code == "duplicate" and "a.csv" in dup.message


def test_worst_severity_and_the_tooltip_text_put_the_serious_first():
    flags = [Flag("spike", WARNING, "a spike"), Flag("time-order", ERROR, "time is wrong"), Flag("x", INFO, "fyi")]
    assert dataqc.worst(flags) == ERROR and dataqc.worst([]) is None
    assert dataqc.describe(flags).splitlines() == ["time is wrong", "a spike", "fyi"]


# ===================================================================== files from every reader are clean

def test_synthetic_files_from_each_instrument_reader_pass_the_checks(tmp_path):
    e, i = synth.cv_scan(1.0, 3.0)
    rows = [(k, k * 0.05, float(e[k]), float(i[k] * 1e-3), 0) for k in range(e.size)]
    synth.write_biologic_mpt(tmp_path / "cv.mpt", "Cyclic Voltammetry", ["mode", "time/s", "Ewe/V", "<I>/mA", "cycle number"],
                             [(2, t, ev, im, c) for _, t, ev, im, c in rows], {"Scan rate (mV/s)": "100"})
    synth.write_chi_txt(tmp_path / "cv.txt", "Cyclic Voltammetry", ["Potential/V", "Current/A"], list(zip(e, i * 1e-6)))
    files = [readers.load(tmp_path / "cv.mpt"), readers.load(tmp_path / "cv.txt")]
    flags = dataqc.check_files(files)
    assert all(not v or v[0].code == "duplicate" for v in flags.values())              # the two files hold the same CV: that is found
    assert [v[0].code for v in flags.values() if v] == ["duplicate"]
