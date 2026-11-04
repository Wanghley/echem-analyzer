"""GUI tests for multi-cycle CV analysis, projects, presets and unsaved-change handling.

All run offscreen on temporary copies of the real data; settings and presets are redirected
to a temp folder so nothing touches the user's real configuration.
"""
import shutil
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from echem_analyzer.gui import main_window as mw
from echem_analyzer.gui.project_dialog import ProjectInfoDialog
from echem_analyzer.presets import PresetStore
from echem_analyzer.project import SUFFIX, ProjectMeta
from helpers import fill_additions, find_ca, find_cv_run, make_window, shot

B2_CSV = "2026-11-02 B2 GY data.csv"
B2_PS = "2026-11-02 B2 GY data.pssession"
B5_CSV = "2026-11-02 B5 CSF C1 data.csv"
A6_PS = "2026-11-02 A6 GY data.pssession"


def critical_calls(monkeypatch) -> list:
    """Record QMessageBox.critical instead of blocking on a modal dialog."""
    calls: list = []
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: calls.append(a[2] if len(a) > 2 else "")))
    return calls


# ===================================================================== multi-cycle CV

def test_cycle_analysis_on_real_data(qapp, workdir, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([workdir])
    cyc = win.cv_tab.cycles

    assert cyc.run_combo.count() == 4                                   # A6, B2 csv, B2 pssession, B5 csv
    run = find_cv_run(win, B2_PS)
    cyc.select_run(run.uid)
    assert cyc.run.uid == run.uid and len(cyc.results) == 5
    assert cyc.rate.value() == pytest.approx(0.05)                      # scan rate read from the pssession
    assert [r.i_pa for r in cyc.results] == pytest.approx([8.925, 10.333, 10.969, 11.359, 11.628], abs=0.01)
    assert cyc.table.rowCount() == 5 and cyc.table.item(2, 1).text() == "3"
    text = cyc.summary_label.text()
    assert "still changing" in text                                     # not conditioned after 5 cycles
    assert "Cycle(s) 1 begin with a large start-up current" in text     # the -140 µA first sample

    # excluding cycle 1 through the table
    cyc.table.item(0, 0).setCheckState(Qt.Unchecked)
    assert cyc.s.excluded_cycles == [1] and len(cyc.results) == 4
    assert "start-up current" not in cyc.summary_label.text()
    assert cyc.table.item(0, 2).text() != "" and cyc.table.item(1, 12).text() == "—"   # first used cycle has no change
    # quick buttons
    cyc._set_excluded(lambda ns: ns[1:-1])
    assert cyc.s.excluded_cycles == [2, 3, 4] and [r.cycle for r in cyc.results] == [1, 5]
    cyc._set_excluded(lambda ns: [])
    assert cyc.s.excluded_cycles == [] and len(cyc.results) == 5


def test_cycle_tab_windows_baseline_and_scan_rate(qapp, workdir, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([workdir])
    cyc = win.cv_tab.cycles
    cyc.select_run(find_cv_run(win, B2_CSV).uid)

    assert cyc.rate.value() == 0 and "Not stored in CSV" in cyc.rate_note.text()   # CSV has no scan rate
    assert np.isnan(cyc.results[1].capacitance)
    assert "scan rate" in [t.get_text() for t in cyc.trend_plot.fig.axes[1].texts][0]
    cyc.rate.setValue(0.05)
    assert cyc.s.scan_rate == 0.05 and np.isfinite(cyc.results[1].capacitance)
    assert len(cyc.trend_plot.fig.axes) == 3                                    # charge axis + twin capacitance axis

    raw = [r.i_pa for r in cyc.results]
    cyc.chk_an.setChecked(True)                                                  # restrict -> starts as the full range
    assert cyc.s.anodic_window == pytest.approx([-0.3, 0.299], abs=0.01)
    assert [r.i_pa for r in cyc.results] == pytest.approx(raw)
    cyc.an_lo.setValue(0.1)
    assert cyc.s.anodic_window[0] == pytest.approx(0.1) and all(r.e_pa >= 0.1 for r in cyc.results)
    cyc.base_combo.setCurrentIndex(cyc.base_combo.findData("linear"))
    assert cyc.s.baseline == "linear"
    assert all(r.i_pa < r.i_pa_raw for r in cyc.results)                         # height measured above the baseline
    cyc.chk_an.setChecked(False)
    assert cyc.s.anodic_window is None and not cyc.an_lo.isEnabled()

    # every display option redraws without error
    for chk in (cyc.chk_branches, cyc.chk_peaks, cyc.chk_excluded, cyc.chk_zoom):
        chk.setChecked(not chk.isChecked())
        chk.setChecked(not chk.isChecked())
    assert cyc.ax_cv.lines


def test_cycle_export_and_empty_state(qapp, workdir, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    cyc = win.cv_tab.cycles
    assert not cyc.panel.isEnabled() and cyc.run is None                          # nothing loaded yet
    win.load_paths([workdir])
    cyc.select_run(find_cv_run(win, B2_PS).uid)
    cyc.table.item(0, 0).setCheckState(Qt.Unchecked)
    out = tmp_path / "cycles.csv"
    assert cyc.export_csv(out) == 5
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("cycle,included,E_pa_V") and len(lines) == 6
    assert lines[1].split(",")[:2] == ["1", "False"] and lines[2].split(",")[:2] == ["2", "True"]


def test_double_clicking_a_cv_scan_opens_its_run(qapp, workdir, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([workdir])
    run = find_cv_run(win, B5_CSV)
    scan3 = run.cycles[3]
    win.cv_tab.show_curve(scan3.uid)
    assert win.cv_tab.cycles.run.uid == run.uid
    assert win.cv_tab.tabs.currentWidget() is win.cv_tab.cycles
    assert scan3.uid in win.cv_tab.overlay._checked


# ===================================================================== dirty tracking

def test_viewing_never_dirties_but_editing_does(qapp, workdir, tmp_path):
    win = make_window(tmp_path / "w")
    s = win.session
    assert not s.dirty and not win.isWindowModified()
    win.load_paths([workdir])
    assert s.dirty and win.isWindowModified()                                     # new data = unsaved change

    proj = tmp_path / "p.echemproj"
    assert win.save_project_to(proj) and not s.dirty and not win.isWindowModified()
    assert win.windowTitle().startswith("p[*]")

    # merely looking around is not a change
    win.ca_tab.select_curve(find_ca(win, B5_CSV).uid)
    win.cv_tab.cycles.select_run(find_cv_run(win, B5_CSV).uid)
    win.ca_tab.smooth_spin.setValue(5)                                            # display-only option
    win.cv_tab.cycles.chk_branches.setChecked(True)
    win.tabs.setCurrentWidget(win.compare_tab)
    assert not s.dirty

    win.ca_tab._binds["stock_conc"].setValue(12.5)
    assert s.dirty and win.isWindowModified()
    assert win.save_project() and not s.dirty

    win.cv_tab.cycles.table.item(0, 0).setCheckState(Qt.Unchecked)
    assert s.dirty
    win.save_project()
    lst = win.compare_tab.list
    ticked = next(i for i in range(lst.count()) if lst.item(i).checkState() == Qt.Checked)
    lst.item(ticked).setCheckState(Qt.Unchecked)                  # a real change (item 0 is unticked by default)
    assert s.dirty


# ===================================================================== project round trip

def test_project_roundtrip_restores_everything_without_the_original_files(qapp, workdir, tmp_path):
    a = make_window(tmp_path / "a")
    a.show()
    a.load_paths([workdir])

    # --- CA: additions, an excluded point, a custom volume
    ca = a.ca_tab
    b2 = find_ca(a, B2_CSV)
    ca.select_curve(b2.uid)
    fill_additions(ca, 505.1, 10.0, 10.0)
    ca.table.item(3, 0).setCheckState(Qt.Unchecked)
    ca.table.item(2, 3).setText("25")
    slope_a = ca.result.fit.slope
    cx_a = ca.result.c_x

    # --- CV: cycle 1 excluded, anodic window, baseline, scan rate typed for the CSV run
    run = find_cv_run(a, B2_CSV)
    cyc = a.cv_tab.cycles
    cyc.select_run(run.uid)
    cyc.rate.setValue(0.05)
    cyc.table.item(0, 0).setCheckState(Qt.Unchecked)
    cyc.chk_an.setChecked(True)
    cyc.an_lo.setValue(0.1)
    cyc.base_combo.setCurrentIndex(cyc.base_combo.findData("linear"))
    ipa_a = [r.i_pa for r in cyc.results]

    # --- selections in the other tabs and the visible tab
    scan2 = find_cv_run(a, B5_CSV).cycles[2]
    a.cv_tab.overlay.check_curve(scan2.uid)
    lst = a.compare_tab.list
    default_ticked = {lst.item(i).data(Qt.UserRole) for i in range(lst.count()) if lst.item(i).checkState() == Qt.Checked}
    ticked = next(i for i in range(lst.count()) if lst.item(i).checkState() == Qt.Checked)
    unticked_uid = lst.item(ticked).data(Qt.UserRole)
    lst.item(ticked).setCheckState(Qt.Unchecked)
    a.cv_tab.tabs.setCurrentIndex(1)
    a.tabs.setCurrentWidget(a.compare_tab)
    a.session.set_meta(ProjectMeta(name="Round trip", operator="W", electrode="B2", analyte="GY",
                                   matrix="CSF", notes="two\nlines"))
    exp_ca = {u: s.to_dict() for u, s in a.session.settings.items()}
    exp_cv = {u: s.to_dict() for u, s in a.session.cv_settings.items()}
    exp_cmp, exp_ovl = set(a.compare_tab._checked), set(a.cv_tab.overlay._checked)

    proj = tmp_path / "work" / f"rt{SUFFIX}"
    assert a.save_project_to(proj) and not a.session.dirty

    shutil.rmtree(workdir)                                                         # raw data is gone
    assert not workdir.exists()

    b = make_window(tmp_path / "b")
    b.show()
    assert b.open_project_from(proj)

    s = b.session
    assert len(s.files) == 4 and not s.dirty and s.project_path == proj
    assert {u: v.to_dict() for u, v in s.settings.items() if u in exp_ca} == exp_ca
    assert {u: v.to_dict() for u, v in s.cv_settings.items()} == exp_cv
    assert s.meta.name == "Round trip" and s.meta.electrode == "B2" and s.meta.notes == "two\nlines"
    assert b.windowTitle().startswith("Round trip[*]") and not b.isWindowModified()

    # visible state restored
    assert b.tabs.currentWidget() is b.compare_tab and b.cv_tab.tabs.currentIndex() == 1
    assert b.ca_tab.curve.uid == b2.uid
    assert b.ca_tab.result.fit.slope == slope_a and b.ca_tab.result.c_x == cx_a            # same numbers
    assert b.ca_tab.s.step_included[2] is False and b.ca_tab.s.step_volumes_ul[1] == 25.0
    assert b.cv_tab.cycles.run.uid == run.uid
    assert b.cv_tab.cycles.s.excluded_cycles == [1] and b.cv_tab.cycles.s.baseline == "linear"
    assert [r.i_pa for r in b.cv_tab.cycles.results] == ipa_a
    assert b.compare_tab._checked == exp_cmp == default_ticked - {unticked_uid}        # the unticked run stayed unticked
    assert b.cv_tab.overlay._checked == exp_ovl and scan2.uid in exp_ovl
    assert [b.compare_tab.list.item(i).checkState() == Qt.Checked for i in range(b.compare_tab.list.count())] \
        == [a.compare_tab.list.item(i).checkState() == Qt.Checked for i in range(a.compare_tab.list.count())]

    # exports go beside the project once the original folder no longer exists
    out = s.analysis_dir(s.curve(b2.uid))
    assert out.parent == proj.parent / "analysis"

    # the file dock lists the restored data
    assert b.file_tree.topLevelItemCount() == 4
    shot(b, qapp, "6_reopened_project")


def test_opening_a_project_replaces_whatever_was_loaded(qapp, workdir, tmp_path):
    a = make_window(tmp_path / "a")
    a.load_paths([workdir / B2_CSV])
    proj = tmp_path / f"one{SUFFIX}"
    a.save_project_to(proj)

    b = make_window(tmp_path / "b")
    b.load_paths([workdir / B5_CSV, workdir / A6_PS])
    b.session.dirty = False                                                        # pretend it was saved
    assert b.open_project_from(proj)
    assert [f.name for f in b.session.files] == [B2_CSV]
    assert b.ca_tab.run_combo.count() == 1


def test_dropping_a_project_file_opens_it_and_extra_data_is_added(qapp, workdir, tmp_path):
    a = make_window(tmp_path / "a")
    a.load_paths([workdir / B2_CSV])
    proj = tmp_path / f"drop{SUFFIX}"
    a.save_project_to(proj)

    b = make_window(tmp_path / "b")
    b.load_paths([proj, workdir / B5_CSV])
    assert [f.name for f in b.session.files] == [B2_CSV, B5_CSV]
    assert b.session.project_path == proj and b.session.dirty                       # added data is unsaved


def test_missing_extension_is_added_on_save(qapp, workdir, tmp_path):
    win = make_window(tmp_path / "w")
    win.load_paths([workdir / B2_CSV])
    assert win.save_project_to(tmp_path / "noext")
    assert (tmp_path / f"noext{SUFFIX}").is_file() and win.session.project_path.name == f"noext{SUFFIX}"


# ===================================================================== unsaved changes / errors

def test_unsaved_changes_prompt(qapp, workdir, tmp_path, monkeypatch):
    win = make_window(tmp_path / "w")
    win.load_paths([workdir])
    monkeypatch.setattr(win, "_ask_save", lambda: mw.CANCEL)
    assert win.new_project() is False and len(win.session.files) == 4               # nothing lost
    assert win.close() is False                                                     # close is vetoed

    proj = tmp_path / f"saved{SUFFIX}"
    monkeypatch.setattr(win, "_ask_save", lambda: mw.SAVE)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(proj), "")))
    assert win.new_project() is True
    assert proj.is_file() and win.session.files == [] and win.session.project_path is None

    win.load_paths([workdir])
    monkeypatch.setattr(win, "_ask_save", lambda: mw.DISCARD)
    assert win.new_project() is True and win.session.files == [] and not win.session.dirty


def test_opening_a_project_asks_before_discarding_unsaved_work(qapp, workdir, tmp_path, monkeypatch):
    a = make_window(tmp_path / "a")
    a.load_paths([workdir / B2_CSV])
    proj = tmp_path / f"other{SUFFIX}"
    a.save_project_to(proj)

    win = make_window(tmp_path / "w")
    win.load_paths([workdir / B5_CSV])                                              # unsaved work
    monkeypatch.setattr(win, "_ask_save", lambda: mw.CANCEL)
    assert win.open_project_from(proj) is False
    assert [f.name for f in win.session.files] == [B5_CSV] and win.session.project_path is None   # kept

    monkeypatch.setattr(win, "_ask_save", lambda: mw.DISCARD)
    assert win.open_project_from(proj) is True
    assert [f.name for f in win.session.files] == [B2_CSV] and win.session.project_path == proj


def test_clean_or_empty_session_does_not_prompt(qapp, workdir, tmp_path, monkeypatch):
    win = make_window(tmp_path / "w")
    monkeypatch.setattr(win, "_ask_save", lambda: pytest.fail("must not prompt"))
    assert win.new_project() is True                                                # nothing to lose
    win.load_paths([workdir])
    win.save_project_to(tmp_path / f"p{SUFFIX}")
    assert win.new_project() is True                                                # saved: nothing to lose


def test_corrupt_project_is_reported_and_leaves_everything_untouched(qapp, workdir, tmp_path, monkeypatch):
    errors = critical_calls(monkeypatch)
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([workdir])
    win.ca_tab.select_curve(find_ca(win, B5_CSV).uid)
    win.ca_tab._binds["stock_conc"].setValue(7.5)
    before = (len(win.session.files), win.ca_tab.curve.uid, win.ca_tab.s.stock_conc, set(win.cv_tab.overlay._checked))
    monkeypatch.setattr(win, "_ask_save", lambda: pytest.fail("a bad file must not trigger the save prompt"))

    bad = tmp_path / f"bad{SUFFIX}"
    bad.write_text("this is not a project")
    assert win.open_project_from(bad) is False
    assert len(errors) == 1 and "not a readable project" in errors[0]
    after = (len(win.session.files), win.ca_tab.curve.uid, win.ca_tab.s.stock_conc, set(win.cv_tab.overlay._checked))
    assert after == before and win.session.dirty                                    # unsaved work still there


def test_save_failure_is_reported(qapp, workdir, tmp_path, monkeypatch):
    errors = critical_calls(monkeypatch)
    win = make_window(tmp_path / "w")
    win.load_paths([workdir / B2_CSV])
    blocker = tmp_path / "file_not_folder"
    blocker.write_text("x")
    assert win.save_project_to(blocker / f"p{SUFFIX}") is False                      # parent is a file
    assert len(errors) == 1 and win.session.dirty and win.session.project_path is None


# ===================================================================== presets

def test_ca_presets_save_apply_delete(qapp, workdir, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([workdir])
    ca = win.ca_tab
    b2, b5 = find_ca(win, B2_CSV), find_ca(win, B5_CSV)
    ca.select_curve(b2.uid)
    fill_additions(ca, 505.1, 10.0, 10.0)
    ca._binds["method"].setCurrentIndex(ca._binds["method"].findData("drift"))
    ca._binds["settle"].setValue(4.0)
    ca.table.item(3, 0).setCheckState(Qt.Unchecked)                                   # run-specific, must not travel
    bar = ca.preset_bar

    assert win.session.presets.path == tmp_path / "w" / "presets.json"                # isolated from the real config
    bar.save_named("GY protocol")
    assert bar.combo.currentText() == "GY protocol"
    assert (tmp_path / "w" / "presets.json").is_file()

    ca.select_curve(b5.uid)
    assert ca._binds["method"].currentData() == "plateau"
    steps_before = list(ca.s.step_times)
    win.session.dirty = False
    assert bar.apply_named("GY protocol", "run") == []
    assert win.session.dirty                                                           # applying is an edit
    assert ca.s.method == "drift" and ca.s.settle == 4.0 and ca.s.stock_conc == 505.1 and ca.s.v0_ml == 10.0
    assert ca._binds["method"].currentData() == "drift" and ca._binds["settle"].value() == 4.0   # controls follow
    assert ca.s.step_times == steps_before and all(ca.s.step_included)                # run data untouched
    assert ca.result.fit is not None

    a6 = next(c for c in win.session.ca_curves() if c.meta.get("run_title", "").startswith("std addition"))
    a6_steps = list(win.session.get_settings(a6).step_times)
    bar.apply_named("GY protocol", "all")
    s6 = win.session.get_settings(a6)
    assert s6.method == "drift" and s6.settle == 4.0 and s6.step_times == a6_steps
    assert win.session.get_settings(b2).step_included[2] is False                     # b2's excluded point survived

    assert bar.delete_named("GY protocol") and bar.combo.count() == 0 and not bar.btn_apply.isEnabled()
    assert PresetStore(tmp_path / "w" / "presets.json").names("ca") == []


def test_presets_persist_across_sessions(qapp, workdir, tmp_path):
    a = make_window(tmp_path / "w")
    a.load_paths([workdir])
    fill_additions(a.ca_tab, 99.0, 5.0, 2.0)
    a.ca_tab.preset_bar.save_named("mine")
    b = make_window(tmp_path / "w")                                                    # same config folder
    assert b.ca_tab.preset_bar.combo.findText("mine") >= 0
    assert b.session.presets.get("ca", "mine")["stock_conc"] == 99.0


def test_cv_presets_carry_the_protocol_and_supply_a_missing_scan_rate(qapp, workdir, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([workdir])
    cyc = win.cv_tab.cycles
    cyc.select_run(find_cv_run(win, B2_PS).uid)
    cyc.chk_an.setChecked(True)
    cyc.an_lo.setValue(0.1)
    cyc.base_combo.setCurrentIndex(cyc.base_combo.findData("linear"))
    cyc.tol.setValue(2.5)
    cyc.table.item(0, 0).setCheckState(Qt.Unchecked)                                   # per-run, must not travel
    cyc.preset_bar.save_named("peak setup")

    cyc.select_run(find_cv_run(win, B5_CSV).uid)                                       # CSV: scan rate unknown
    assert cyc.s.scan_rate == 0 and cyc.s.excluded_cycles == []
    cyc.preset_bar.apply_named("peak setup", "run")
    assert cyc.s.anodic_window[0] == pytest.approx(0.1) and cyc.s.baseline == "linear" and cyc.s.stability_tol == 2.5
    assert cyc.s.scan_rate == 0.05 and np.isfinite(cyc.results[1].capacitance)         # supplied by the preset
    assert cyc.s.excluded_cycles == []                                                 # cycle choice stayed per run
    assert cyc.chk_an.isChecked() and cyc.base_combo.currentData() == "linear"          # controls follow


# ===================================================================== misc project features

def test_export_all_results(qapp, workdir, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([workdir])
    win.ca_tab.select_curve(find_ca(win, B2_CSV).uid)
    fill_additions(win.ca_tab)
    win.cv_tab.cycles.select_run(find_cv_run(win, B2_PS).uid)
    written = win.export_all_results(tmp_path / "out")
    rel = {p.relative_to(tmp_path / "out").as_posix() for p in written}
    assert {"ca_summary.csv", "cv_summary.csv"} <= rel
    assert any(r.startswith("ca/") and r.endswith("__steps.csv") for r in rel)
    assert any(r.startswith("cv/") and r.endswith("__cycles.csv") for r in rel)
    summary = (tmp_path / "out" / "ca_summary.csv").read_text(encoding="utf-8")
    assert "B2 GY data.csv" in summary


def test_recent_projects_menu(qapp, workdir, tmp_path):
    win = make_window(tmp_path / "w")
    win.load_paths([workdir / B2_CSV])
    p1, p2 = tmp_path / f"one{SUFFIX}", tmp_path / f"two{SUFFIX}"
    win.save_project_to(p1)
    win.save_project_to(p2)
    assert win.session.recent_projects() == [str(p2), str(p1)]                         # most recent first
    p1.unlink()
    assert win.session.recent_projects() == [str(p2)]                                  # vanished files drop out
    win._populate_recent_menu()
    assert [a.text() for a in win.recent_menu.actions()] == [p2.name]


def test_project_info_dialog_and_title(qapp, workdir, tmp_path):
    win = make_window(tmp_path / "w")
    meta = ProjectMeta(name="Old", operator="a", notes="n")
    dlg = ProjectInfoDialog(meta)
    dlg.edits["name"].setText("  CSF run  ")
    dlg.edits["electrode"].setText("B5")
    dlg.notes.setPlainText("hello\nworld ")
    out = dlg.result_meta()
    assert (out.name, out.electrode, out.operator, out.notes) == ("CSF run", "B5", "a", "hello\nworld")
    assert meta.name == "Old"                                                          # the original is not mutated

    win.session.set_meta(out)
    assert win.session.dirty and win.windowTitle() == "CSF run[*] — Echem Analyzer"


def test_cycle_tab_screenshot(qapp, workdir, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([workdir])
    win.tabs.setCurrentWidget(win.cv_tab)
    cyc = win.cv_tab.cycles
    cyc.select_run(find_cv_run(win, B2_PS).uid)
    cyc.chk_branches.setChecked(True)
    shot(win, qapp, "7_cv_cycles")
    cyc.table.item(0, 0).setCheckState(Qt.Unchecked)
    cyc.chk_an.setChecked(True)
    cyc.an_lo.setValue(0.0)
    cyc.base_combo.setCurrentIndex(cyc.base_combo.findData("linear"))
    shot(win, qapp, "8_cv_cycles_baseline")
