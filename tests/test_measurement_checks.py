"""The import checks as people see them: a Checks column in Measurements, a tooltip, and the details beside the preview."""
from pathlib import Path

import numpy as np

import synth
from echem_analyzer.gui.measurements_tab import check_summary
from echem_analyzer.dataqc import ERROR, WARNING, Flag
from helpers import make_window


def staircase(seed=2):
    t = np.linspace(0, 200, 801)
    return t, 1.0 + 0.5 * np.minimum(np.floor(t / 40), 4) + np.random.default_rng(seed).normal(0, 0.01, t.size)


def write_ca(path, y, t=None):
    t = staircase()[0] if t is None else t
    synth.write_pstrace_csv(path, [("CA: i vs t", t, y, "s", "µA")])


def rows_by_file(win):
    """{file name: table row}, found through each row's curve rather than the shortened name shown in the table."""
    return {Path(c.source).name: r for r, c in enumerate(win.measurements_tab._curves)}


def test_the_checks_column_says_ok_for_good_data_and_explains_the_rest(qapp, tmp_path):
    t, y = staircase()
    spiky = y.copy()
    spiky[-1] += 3.0                                                    # PalmSens' end-of-run spike
    write_ca(tmp_path / "good.csv", y)
    write_ca(tmp_path / "spiky.csv", spiky)
    write_ca(tmp_path / "zgood copy.csv", y)                            # the same run saved again
    win = make_window(tmp_path / "w")
    win.load_paths([tmp_path / n for n in ("good.csv", "spiky.csv", "zgood copy.csv")])
    tab = win.measurements_tab
    rows = rows_by_file(win)
    cell = lambda name: tab.table.item(rows[name], 1)                                      # noqa: E731
    assert cell("good.csv").text() == "OK"
    assert cell("spiky.csv").text() == "1 warning" and "spike" in cell("spiky.csv").toolTip()
    assert cell("zgood copy.csv").text() == "1 warning" and "Identical to" in cell("zgood copy.csv").toolTip()


def test_selecting_a_flagged_measurement_shows_why_beside_the_plot(qapp, tmp_path):
    t, y = staircase()
    clipped = np.minimum(y, 2.0)                                       # the current range was too small
    write_ca(tmp_path / "ok.csv", y)
    write_ca(tmp_path / "clipped.csv", clipped)
    win = make_window(tmp_path / "w")
    win.load_paths([tmp_path / "ok.csv", tmp_path / "clipped.csv"])
    tab = win.measurements_tab
    rows = rows_by_file(win)
    tab.table.selectRow(rows["clipped.csv"])
    assert not tab.checks_notice.isHidden() and "stays at exactly" in tab.checks_notice.text()
    tab.table.selectRow(rows["ok.csv"])
    assert tab.checks_notice.isHidden()


def test_changing_the_technique_re_runs_the_checks(qapp, tmp_path):
    t = np.linspace(0, 120, 121)
    synth.write_pstrace_csv(tmp_path / "e.csv", [("CA: i vs t", t, 0.25 + 3e-3 / 60 * t, "s", "µA")])    # a drifting potential, read as a CA
    win = make_window(tmp_path / "w")
    win.load_paths([tmp_path / "e.csv"])
    curve = win.session.all_curves()[0]
    assert "drift" not in [f.code for f in win.session.data_flags()[curve.uid]]
    curve.y_unit = "V"
    win.session.set_technique(curve.uid, "OCP")                         # now it is an open-circuit potential that has not settled
    assert "drift" in [f.code for f in win.session.data_flags()[curve.uid]]
    assert win.measurements_tab.table.item(0, 1).text() == "1 warning"


def test_summary_text():
    assert check_summary([]) == "OK"
    assert check_summary([Flag("a", WARNING, "x")]) == "1 warning"
    assert check_summary([Flag("a", WARNING, "x"), Flag("b", WARNING, "y"), Flag("c", ERROR, "z")]) == "1 error, 2 warnings"
