"""Drives the real GUI offscreen on *copies* of the data (nothing is written to the data folder).

Set ECHEM_SHOT_DIR to a directory to also save a screenshot of every tab.
"""
import csv
import json
from pathlib import Path

import numpy as np
import pytest
from matplotlib.backend_bases import MouseEvent
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from echem_analyzer.gui.session import Session
from echem_analyzer.models import KIND_CA
from echem_analyzer.reporting import analysis_dir_for
from helpers import fill_additions, find_ca, make_window, shot


def test_full_workflow(qapp, workdir, tmp_path):
    win = make_window(tmp_path)
    win.show()
    win.load_paths([workdir])
    ca = win.ca_tab

    # ---- everything loaded, CA run auto-selected with 5 detected steps
    assert len(win.session.files) == 4
    assert win.file_tree.topLevelItemCount() == 4
    b2 = find_ca(win, "2026-11-02 B2 GY data.csv")
    ca.select_curve(b2.uid)
    assert ca.curve is b2
    assert len(ca.s.step_times) == 5
    assert ca.table.rowCount() == 6
    assert ca.result.fit is None                     # no volumes yet -> hint, no fit
    assert "Enter stock" in ca.results_label.text()
    shot(win, qapp, "0_before_inputs")

    # ---- fill in the additions via the real widgets
    fill_additions(ca)
    r = ca.result
    assert r.fit is not None and r.fit.n == 6
    assert r.sensitivity > 0 and np.isfinite(r.lod) and np.isfinite(r.c_x)
    assert "Slope" in ca.results_label.text()
    shot(win, qapp, "1_ca_tab")

    # defaults were remembered for the next run
    assert win.session.defaults()["stock_conc"] == pytest.approx(505.1)

    # ---- switching method changes the answer and keeps the app consistent
    slope_plateau = r.fit.slope
    idx = ca._binds["method"].findData("drift")
    ca._binds["method"].setCurrentIndex(idx)
    assert ca.s.method == "drift"
    assert ca.result.fit.slope != pytest.approx(slope_plateau, rel=1e-3)
    ca._binds["method"].setCurrentIndex(ca._binds["method"].findData("plateau"))
    assert ca.result.fit.slope == pytest.approx(slope_plateau)

    # ---- table edits: exclude a point, override a concentration, edit a volume
    n_fit = ca.result.fit.n
    ca.table.item(3, 0).setCheckState(Qt.Unchecked)
    assert ca.s.step_included[2] is False and ca.result.fit.n == n_fit - 1
    ca.table.item(3, 0).setCheckState(Qt.Checked)
    assert ca.result.fit.n == n_fit

    ca.table.item(2, 3).setText("25")
    assert ca.s.step_volumes_ul[1] == 25.0
    ca.table.item(2, 5).setText("0.123")
    assert ca.s.step_conc_override[1] == pytest.approx(0.123)
    assert ca.result.conc_added[2] == pytest.approx(0.123)
    ca.table.item(2, 5).setText("")
    assert ca.s.step_conc_override[1] is None
    ca.table.item(2, 3).setText("10")

    # ---- move a step by editing its time (list order must stay aligned)
    t0 = ca.s.step_times[0]
    ca.s.step_volumes_ul[0] = 77.0
    ca.table.item(1, 2).setText(f"{t0 + 0.4:.2f}")
    assert ca.s.step_times[0] == pytest.approx(t0 + 0.4, abs=0.01)     # typed with 2 decimals
    assert ca.s.step_volumes_ul[0] == 77.0
    ca.s.step_volumes_ul[0] = 10.0
    ca.recompute()

    # ---- drag a step marker with real (synthetic) mouse events
    canvas = ca.ca_plot.canvas
    canvas.draw()
    ax = ca.ax_ca
    before = list(ca.s.step_times)
    x_pix, y_pix = ax.transData.transform((before[2], np.mean(ax.get_ylim())))
    dx = ax.transData.transform((before[2] + 1.5, 0))[0] - x_pix        # +1.5 s in pixels
    for name, x in (("button_press_event", x_pix), ("motion_notify_event", x_pix + dx),
                    ("button_release_event", x_pix + dx)):
        canvas.callbacks.process(name, MouseEvent(name, canvas, x, y_pix, button=1))
    assert ca.s.step_times[2] == pytest.approx(before[2] + 1.5, abs=0.2)     # layout shifts a few px as the ghost line is added
    assert len(ca.s.step_times) == 5

    # double-click adds, right-click removes
    canvas.draw()
    x_new, y_new = ax.transData.transform((60.0, np.mean(ax.get_ylim())))
    ev = MouseEvent("button_press_event", canvas, x_new, y_new, button=1, dblclick=True)
    canvas.callbacks.process("button_press_event", ev)
    assert len(ca.s.step_times) == 6 and ca.s.step_times == sorted(ca.s.step_times)
    assert len(ca.s.step_volumes_ul) == len(ca.s.step_included) == 6
    canvas.draw()
    x_hit, y_hit = ax.transData.transform((60.0, np.mean(ax.get_ylim())))
    canvas.callbacks.process("button_press_event", MouseEvent("button_press_event", canvas, x_hit, y_hit, button=3))
    assert len(ca.s.step_times) == 5

    # ---- zoom/pan tool disables marker editing
    ca.ca_plot.toolbar.zoom()
    assert ca.ca_plot.toolbar_active()
    canvas.callbacks.process("button_press_event", MouseEvent("button_press_event", canvas, x_new, y_new, button=1, dblclick=True))
    assert len(ca.s.step_times) == 5
    ca.ca_plot.toolbar.zoom()                         # toggle off
    assert not ca.ca_plot.toolbar_active()

    # ---- export, then settings are reloaded by a brand new session
    out_dir = analysis_dir_for(b2)
    assert tmp_path in out_dir.parents                # exporting into the temp copy, not Drive
    files = {p.name for p in ca.export_to(out_dir)}
    assert files == {"steps.csv", "results.csv", "settings.json", "ca_plot.png", "ca_plot.svg",
                     "calibration.png", "calibration.svg"}
    assert (out_dir / "ca_plot.png").stat().st_size > 5000
    with (out_dir / "steps.csv").open() as fh:
        rows = list(csv.reader(fh))
    assert len(rows) == 7 and rows[0][0] == "segment"
    saved = json.loads((out_dir / "settings.json").read_text())
    assert saved["stock_conc"] == pytest.approx(505.1) and len(saved["step_times"]) == 5

    (tmp_path / "second").mkdir()
    win2 = make_window(tmp_path / "second")
    win2.load_paths([workdir])
    s2 = win2.session.get_settings(find_ca(win2, "2026-11-02 B2 GY data.csv"))
    assert s2.step_times == pytest.approx(ca.s.step_times)
    assert s2.step_volumes_ul == ca.s.step_volumes_ul

    # copy to clipboard produces text
    ca.copy_results()
    assert "Slope" in QApplication.clipboard().text()


def test_pssession_titles_and_stock_prefill(qapp, workdir, tmp_path):
    win = make_window(tmp_path)
    win.load_paths([workdir])
    run = next(c for c in win.session.ca_curves() if c.meta.get("run_title", "").startswith("std addition"))
    s = win.session.get_settings(run)
    assert s.stock_conc == pytest.approx(505.1) and s.stock_unit == "mM"
    win.ca_tab.select_curve(run.uid)
    assert win.ca_tab.curve is run


def test_cv_and_compare_tabs(qapp, workdir, tmp_path):
    win = make_window(tmp_path)
    win.show()
    win.load_paths([workdir])

    cv = win.cv_tab.overlay
    assert len(cv.checked_curves()) == 5               # CV scans of the first loaded file
    assert cv.table.rowCount() == 5
    win.tabs.setCurrentWidget(win.cv_tab)
    win.cv_tab.tabs.setCurrentWidget(cv)
    cv.color_combo.setCurrentIndex(cv.color_combo.findData("file"))
    b2_scan = next(c for c in win.session.all_curves()
                   if Path(c.source).name == "2026-11-02 B2 GY data.csv" and c.scan == 2)
    cv.check_curve(b2_scan.uid)
    assert len(cv.checked_curves()) == 6
    p = tmp_path / "cv.csv"
    assert cv.export_csv(p) == 6
    head = p.read_text(encoding="utf-8").splitlines()[:2]
    assert "CV i vs E Scan" in head[0] and "V" in head[1]
    # auto-zoom ignores the -133 uA start-up transient of scan 1; unticking shows everything
    assert cv.ax.get_ylim()[0] > -50
    cv.chk_zoom.setChecked(False)
    assert cv.ax.get_ylim()[0] < -100
    cv.chk_zoom.setChecked(True)
    shot(win, qapp, "2_cv_tab")

    cmp_ = win.compare_tab
    # default selection: only runs that look like a staircase, and no duplicates (the B2 run is in
    # both the .csv and the .pssession; A6 'calibration' is a smooth decay, B2's first run too)
    checked = cmp_.checked_curves()
    keys = {(c.n, round(float(c.y[0]), 6)) for c in checked}
    assert len(keys) == len(checked) == 3
    assert {Path(c.source).name for c in checked} == {
        "2026-11-02 A6 GY data.pssession", "2026-11-02 B2 GY data.csv", "2026-11-02 B5 CSF C1 data.csv"}
    assert not any(c.meta["run_title"] == "calibration" for c in checked)
    n_ca = len(checked)
    for name in ("2026-11-02 B2 GY data.csv", "2026-11-02 B5 CSF C1 data.csv"):
        win.ca_tab.select_curve(find_ca(win, name).uid)
        fill_additions(win.ca_tab)
    cmp_.replot()
    assert cmp_.table.rowCount() == n_ca
    names = [cmp_.table.item(i, 0).text() for i in range(cmp_.table.rowCount())]
    sens = {n: cmp_.table.item(i, 3).text() for i, n in enumerate(names)}
    assert sens["B2 GY (csv)"] != "—" and sens["B5 CSF C1 (csv)"] != "—"
    p = tmp_path / "cmp.csv"
    assert cmp_.export_csv(p) == n_ca
    assert p.read_text(encoding="utf-8").splitlines()[0].startswith("File,Run,Method")
    win.tabs.setCurrentWidget(cmp_)
    shot(win, qapp, "3_compare_tab")


def test_bad_file_is_reported_not_fatal(qapp, tmp_path):
    bad = tmp_path / "broken.csv"
    bad.write_text("not,a,palmsens,file\n", encoding="utf-8")
    sess = Session(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat))
    loaded, errors = sess.add_paths([bad, tmp_path / "x.txt"])
    assert loaded == [] and len(errors) == 2


def test_empty_state(qapp, tmp_path):
    win = make_window(tmp_path)
    win.show()
    qapp.processEvents()
    assert win.ca_tab.curve is None and not win.ca_tab.panel.isEnabled()
    shot(win, qapp, "4_empty")


def test_a6_run_shows_ignored_event_warning(qapp, workdir, tmp_path):
    """The A6 run has an upward jump between additions; the user must be told, not misled."""
    win = make_window(tmp_path)
    win.show()
    win.load_paths([workdir])
    run = next(c for c in win.session.ca_curves() if c.meta.get("run_title", "").startswith("std addition"))
    ca = win.ca_tab
    ca.select_curve(run.uid)
    assert len(ca.s.step_times) == 4
    assert len(ca.s.ignored_events) == 1 and ca.s.ignored_events[0] == pytest.approx(36.0, abs=0.5)
    assert "opposite direction" in ca.results_label.text()
    fill_additions(ca)
    assert "opposite direction" in ca.results_label.text()       # still shown once a fit exists
    shot(win, qapp, "5_a6_warning")


def test_calibration_decay_run_has_no_steps_in_gui(qapp, workdir, tmp_path):
    win = make_window(tmp_path)
    win.load_paths([workdir])
    cal = next(c for c in win.session.ca_curves() if c.meta.get("run_title") == "calibration")
    win.ca_tab.select_curve(cal.uid)
    assert win.ca_tab.s.step_times == [] and win.ca_tab.table.rowCount() == 1
