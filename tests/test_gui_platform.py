"""The platform end to end through the GUI: measurements -> analysis -> responses -> sensor study -> project."""
import math
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox

import synth
from echem_analyzer import electrode as electrode_engine
from echem_analyzer import theme
from echem_analyzer.gui import main_window as mw
from echem_analyzer.gui.import_dialog import ImportDialog
from echem_analyzer.project import SUFFIX
from helpers import make_window, shot

VOLT, AMP, SEC, OHM = ("PalmSens.Units.Volt", "PalmSens.Units.MicroAmpere", "PalmSens.Units.Second", "PalmSens.Units.Ohm")
CONCS = [1, 2, 5, 10, 20, 50]
AREA_TRUE, CONC_MM, D = 0.05, 5.0, 7.6e-6


@pytest.fixture()
def lab(tmp_path) -> Path:
    """A folder of synthetic measurements in the PalmSens layouts: DPV series, EIS, OCP and a CV scan-rate series."""
    d = tmp_path / "lab"
    d.mkdir()
    rng = np.random.default_rng(3)
    ms = [{"title": "DPV blank", "method_id": "dpv", "curves": [("DPV i vs E", *(lambda e, i: (e, i + rng.normal(0, 0.01, i.size)))(*synth.dpv_trace(0.0)), VOLT, AMP)]}]
    for c in CONCS:
        e, i = synth.dpv_trace(c)
        ms.append({"title": f"DPV {c} µM", "method_id": "dpv", "curves": [("DPV i vs E", e, i + rng.normal(0, 0.01, i.size), VOLT, AMP)]})
    synth.write_pssession(d / "dpv_series.pssession", ms)
    f_hz, z = synth.randles_spectrum(rct=1000.0, noise=0.003)
    synth.write_pssession(d / "eis.pssession", [{"title": "Rct sensor 5 µM", "method_id": "eis", "eis": (f_hz, z)}])
    t = np.arange(0, 60, 0.1)
    synth.write_pstrace_csv(d / "ocp.csv", [("Open Circuit Potential: OCP E vs t", t, 0.25 + 1e-5 * t, "s", "V")])
    slope = electrode_engine.randles_sevcik_slope(1, AREA_TRUE, CONC_MM, D)
    cvs = []
    for nu in (0.01, 0.025, 0.05, 0.1, 0.2, 0.4):
        e, i = synth.cv_scan(nu, slope)
        cvs.append({"title": f"CV {nu} V/s", "method_id": "cv", "method": {"SCAN_RATE": nu}, "curves": [("CV i vs E Scan 1", e, i, VOLT, AMP)]})
    synth.write_pssession(d / "scanrate.pssession", cvs)
    return d


def dpv_curves(win):
    return [c for c in win.session.all_curves() if c.kind == "DPV"]


# ===================================================================== shell and measurements

def test_all_workspaces_exist_and_synthetic_files_are_recognised(qapp, lab, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    assert [win.tabs.tabText(i) for i in range(win.tabs.count())] == [
        "Measurements", "Standard addition", "Voltammetry", "Analysis", "Electrode", "Sensor study", "Compare"]
    win.load_paths([lab])
    kinds = sorted(c.kind for c in win.session.all_curves())
    assert kinds.count("DPV") == 7 and kinds.count("EIS") == 1 and kinds.count("OCP") == 1 and kinds.count("CV") == 6
    assert win.tabs.currentWidget() is win.measurements_tab                      # no CA in these files: land on the list
    assert win.measurements_tab.table.rowCount() == 15
    assert f"{len(win.session.all_curves())} measurements" in win.count_label.text()
    win.measurements_tab.table.selectRow(2)                                              # shows the preview pane
    shot(win, qapp, "20_measurements")


def test_technique_can_be_corrected_in_the_measurements_table(qapp, lab, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([lab])
    mt = win.measurements_tab
    row = next(i for i, c in enumerate(mt._curves) if c.kind == "OCP")
    uid = mt._curves[row].uid
    mt.table.cellWidget(row, 0).setCurrentIndex(mt.table.cellWidget(row, 0).findData("CP"))
    assert win.session.curve(uid).kind == "CP" and win.session.dirty
    win.analysis_tab.select_measurement(uid)
    assert win.analysis_tab.an_combo.count() >= 2 and win.analysis_tab.curve.kind == "CP"


# ===================================================================== analysis -> responses

def test_dpv_analysis_to_study_and_calibration(qapp, lab, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([lab])
    at = win.analysis_tab
    first = next(c for c in dpv_curves(win) if "10 µM" in c.meta["run_title"])
    win.open_in_analysis(first.uid)
    assert win.tabs.currentWidget() is at and at.analyzer.key == "peak" and at.result.ok
    assert at.result.responses[0].value == pytest.approx(8.0, rel=0.05)           # 0.8 µA/µM · 10 µM
    assert [k.label.text() for k in at.kpis if k.isVisibleTo(at)][:2] == ["PEAK HEIGHT", "PEAK POTENTIAL"]

    # changing a setting re-runs the analysis and is remembered per measurement
    at._widgets["use_window"].setChecked(True)
    at._widgets["win_lo"].setValue(0.0)
    at._widgets["win_hi"].setValue(0.45)
    assert at.params["use_window"] and win.session.get_params("peak", first.uid)["win_hi"] == 0.45
    assert at.result.responses[0].value == pytest.approx(8.0, rel=0.05)

    assert at.send_to_study() == 2 and len(win.session.responses) == 2
    n = at.send_all()
    assert n == 14 and len(win.session.responses) == 14                            # 7 DPV × (height + potential); upserted, not duplicated
    heights = [r for r in win.session.responses if r.quantity == "Peak height"]
    assert sorted(r.conc for r in heights if r.conc is not None) == CONCS          # concentrations parsed from the titles
    blank = next(r for r in heights if r.conc is None)
    win.session.update_response(blank.id, role="blank")

    cal = win.study_tab.calibration
    win.study_tab.tabs.setCurrentWidget(cal)
    cal.refresh()
    cs = cal.study
    assert cs.ok and cs.conc_unit == "µM" and cs.options.quantity == "Peak height"
    cs_lin = cal.study
    cal.model.setCurrentIndex(cal.model.findData("linear"))
    cal.restrict.setChecked(False)
    cs = cal.study
    assert cs.sensitivity == pytest.approx(0.8, rel=0.04) and cs.fit.r2 > 0.999 and cs.lod > 0
    assert float(cal.kpis[0].value.text()) == pytest.approx(0.8, rel=0.04) and cal.kpis[0].sub.text() == "µA/µM"
    assert cal.table.rowCount() == 4 and cal.plot.fig.axes
    win.tabs.setCurrentWidget(win.study_tab)
    shot(win, qapp, "21_calibration")


def test_responses_table_edits_flow_into_the_study(qapp, lab, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([lab])
    win.analysis_tab.select_measurement(dpv_curves(win)[3].uid)
    win.analysis_tab.send_all()
    rp = win.study_tab.responses
    assert rp.table.rowCount() == 14 and not rp.empty.isVisibleTo(rp)
    heights = [i for i, r in enumerate(rp._rows) if r.quantity == "Peak height"]
    row = heights[2]
    rp.table.item(row, 6).setText("123")                                           # concentration
    assert win.session.responses[row].conc == 123.0
    rp.table.item(row, 8).setText("ascorbate")                                     # condition
    assert win.session.responses[row].condition == "ascorbate"
    rp.table.item(row, 9).setText("E7")                                            # group
    assert win.session.responses[row].group == "E7"
    rp.table.item(row, 11).setText("nonsense")                                     # an invalid role is ignored
    assert win.session.responses[row].role == "standard"
    rp.table.item(row, 0).setCheckState(Qt.Unchecked)
    assert win.session.responses[row].use is False
    rp.table.item(row, 6).setText("not a number")
    assert win.session.responses[row].conc == 123.0                                # stays as it was
    rp.table.selectRow(row)
    rp.remove()
    assert len(win.session.responses) == 13
    win.session.add_manual_response("Peak height", "µA", 4.2, conc=5.0, group="X")
    assert win.session.responses[-1].id == "manual:1" and win.session.dirty


def test_eis_analysis_recovers_rct_and_feeds_the_study(qapp, lab, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([lab])
    eis_curve = next(c for c in win.session.all_curves() if c.kind == "EIS")
    win.open_in_analysis(eis_curve.uid)
    at = win.analysis_tab
    assert at.analyzer.key == "eis_fit" and at.result.ok
    q = {s.quantity: s.value for s in at.result.responses}
    assert q["Rct"] == pytest.approx(1000.0, rel=0.05) and q["Rs"] == pytest.approx(100.0, rel=0.06)
    at._widgets["area_cm2"].setValue(0.07)
    at._widgets["conc_mM"].setValue(5.0)
    assert any(s.quantity == "k0" for s in at.result.responses)
    at.send_to_study()
    rct = next(r for r in win.session.responses if r.quantity == "Rct")
    assert rct.conc == 5.0 and rct.conc_unit == "µM" and rct.technique == "EIS"       # "5 µM" read from the run title
    shot(win, qapp, "22_eis")


def test_ocp_steady_state_analysis(qapp, lab, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([lab])
    ocp = next(c for c in win.session.all_curves() if c.kind == "OCP")
    win.open_in_analysis(ocp.uid)
    at = win.analysis_tab
    at.an_combo.setCurrentIndex(at.an_combo.findData("steady"))
    assert at.result.ok and at.result.responses[0].value == pytest.approx(0.25 + 1e-5 * 55, abs=1e-4)
    assert not any("drifting" in w for w in at.result.warnings)                       # 0.24 %/min: below the 1 % warning
    assert at.table_card.isHidden()


# ===================================================================== import

def test_importing_an_arbitrary_table(qapp, tmp_path, monkeypatch):
    win = make_window(tmp_path / "w")
    win.show()
    f = tmp_path / "gamry_export.txt"
    e, i = synth.dpv_trace(7.0)
    f.write_text("Potential (mV)\tCurrent (nA)\n" + "\n".join(f"{a * 1000:.3f}\t{b * 1000:.3f}" for a, b in zip(e, i)), encoding="utf-8")
    def choose_dpv_and_accept(self):
        assert self.tech.currentData() == "OTHER"                                       # unrecognised: generic x-y by default
        self.tech.setCurrentIndex(self.tech.findData("DPV"))
        self._accept()
        return self.curve is not None
    monkeypatch.setattr(ImportDialog, "exec", choose_dpv_and_accept)
    assert win.import_table_from(f)
    c = win.session.all_curves()[0]
    assert c.kind == "DPV" and c.x_unit == "V" and c.y_unit == "µA" and c.meta["imported"]
    assert np.allclose(c.x, e, atol=1e-6) and np.allclose(c.y, i, atol=1e-6)         # mV and nA converted back
    assert win.session.dirty and win.stack.currentIndex() == mw.PAGE_WORK
    win.open_in_analysis(c.uid)
    assert win.analysis_tab.result.ok


def test_import_dialog_reports_problems_instead_of_crashing(qapp, tmp_path):
    bad = tmp_path / "words.txt"
    bad.write_text("nothing numeric in here\nat all", encoding="utf-8")
    dlg = ImportDialog(bad)
    assert not dlg.box.button(QDialogButtonBox.Ok).isEnabled() and "Could not read" in dlg.notice.text()
    f = tmp_path / "eis.txt"
    f_hz, z = synth.randles_spectrum()
    f.write_text("f (Hz)\tZ' (Ohm)\t-Z'' (Ohm)\n" + "\n".join(f"{a}\t{b.real}\t{-b.imag}" for a, b in zip(f_hz, z)), encoding="utf-8")
    dlg = ImportDialog(f)
    assert dlg.tech.currentData() == "EIS" and set(dlg.role_boxes) == {"freq", "zre", "zim"}
    dlg.role_boxes["zim"].setCurrentIndex(0)                                          # leave a required column unassigned
    dlg._accept()
    assert dlg.curve is None and "not assigned" in dlg.notice.text()


# ===================================================================== electrode

def test_scan_rate_study_recovers_the_electroactive_area(qapp, lab, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([lab])
    et = win.electrode_tab
    runs = [r for r in win.session.cv_runs() if "scanrate" in Path(r.source).name]
    assert len(runs) == 6 and all(win.session.get_cv_settings(r).scan_rate > 0 for r in runs)      # read from the method
    st = et.sr_runs.state()
    for r in runs:
        st["use"][r.uid] = True
    et.conc.setValue(CONC_MM)
    et.diff.setValue(D)
    et.area.setValue(0.0314)
    a = et.analysis
    assert a is not None and a.fit_a.r2 > 0.999 and a.loglog_a == pytest.approx(0.5, abs=0.02)
    assert a.ecsa_a == pytest.approx(AREA_TRUE, rel=0.03) and a.ecsa_c == pytest.approx(AREA_TRUE, rel=0.03)
    assert et.sr_kpis[0].value.text().startswith("0.05") and "diffusion" in et.sr_kpis[2].sub.text()
    assert a.roughness == pytest.approx(AREA_TRUE / 0.0314, rel=0.04)
    et.sr_group.setText("E1")
    assert et.send_scan_rate() == 3
    ecsa = next(r for r in win.session.responses if r.quantity == "ECSA")
    assert ecsa.group == "E1" and ecsa.value == pytest.approx(AREA_TRUE, rel=0.03) and ecsa.unit == "cm²"
    et.send_scan_rate()                                                                # sending again updates, never duplicates
    assert len([r for r in win.session.responses if r.quantity == "ECSA"]) == 1
    win.tabs.setCurrentWidget(win.electrode_tab)
    shot(win, qapp, "23_electrode")


def test_visiting_the_electrode_tab_does_not_mark_cv_runs_as_analysed(qapp, lab, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([lab])
    before = set(win.session.cv_settings)                                              # the Voltammetry tab opens its first run itself
    assert len(before) <= 1
    win.tabs.setCurrentWidget(win.electrode_tab)
    win.electrode_tab.recompute()
    assert set(win.session.cv_settings) == before                                      # looking at six runs is not analysing them
    from echem_analyzer.report import build_report
    assert len([s for s in build_report(win.session.to_project_data()).sections if s.kind == "cv"]) <= 1
    run = win.session.cv_runs()[0]
    win.electrode_tab.sr_runs.table.item(0, 2).setText("0.5")                          # typing a scan rate does store settings
    assert win.session.cv_settings[run.uid].scan_rate == 0.5


def test_cell_widgets_are_placed_immediately_after_a_rebuild(qapp, lab, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([lab])
    for table, col in ((win.electrode_tab.sr_runs.table, 3), (win.measurements_tab.table, 0)):
        if table is win.electrode_tab.sr_runs.table:
            win.electrode_tab.sr_runs.rebuild()
        else:
            win.measurements_tab.rebuild()
        for r in range(table.rowCount()):                                              # no event-loop turn in between
            w = table.cellWidget(r, col)
            assert w.geometry().y() >= table.rowViewportPosition(r) and w.geometry().x() >= table.horizontalHeader().sectionViewportPosition(col)


def test_scan_rates_can_be_typed_for_runs_without_one(qapp, tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    slope = electrode_engine.randles_sevcik_slope(1, AREA_TRUE, CONC_MM, D)
    synth.write_pstrace_csv(d / "cv.csv", [(f"Cyclic Voltammetry: CV i vs E Scan {k + 1}", *synth.cv_scan(nu, slope), "V", "µA")
                                           for k, nu in enumerate((0.02, 0.05, 0.1, 0.2))])
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([d])
    et = win.electrode_tab
    assert et.analysis is None                                                          # nothing ticked yet
    # a CSV holds the cycles of ONE run, so a scan-rate study needs one run per rate: one cycle here -> no study
    assert len(win.session.cv_runs()) == 1 and win.session.get_cv_settings(win.session.cv_runs()[0]).scan_rate == 0.0
    et.sr_runs.table.item(0, 2).setText("0.05")
    assert win.session.get_cv_settings(win.session.cv_runs()[0]).scan_rate == 0.05


# ===================================================================== reproducibility / stability / persistence

def test_study_panels_react_to_edits_and_the_project_restores_everything(qapp, lab, tmp_path):
    a = make_window(tmp_path / "a")
    a.show()
    a.load_paths([lab])
    a.analysis_tab.select_measurement(dpv_curves(a)[2].uid)
    a.analysis_tab._widgets["smooth"].setValue(5)                                          # an edit is stored per measurement
    a.analysis_tab.send_all()
    s = a.session
    heights = [r for r in s.responses if r.quantity == "Peak height" and r.conc is not None]
    for r in heights:                                                                    # two electrodes, the second 5 % less sensitive
        s.update_response(r.id, group="E1")
        s.add_manual_response("Peak height", "µA", r.value * 0.95, conc=r.conc, conc_unit="µM", group="E2")
    for k, t in enumerate((0, 7, 14, 21, 28)):                                           # the 10 µM point measured over time
        s.add_manual_response("Peak height", "µA", 8.0 * math.exp(-t / 30), conc=10.0, conc_unit="µM", group="E1", time=t)
    s.add_manual_response("Peak height", "µA", 0.8 * 7.0, conc=7.0, conc_unit="µM", group="E1", role="sample")
    st = a.study_tab

    st.repro.refresh()
    st.repro.time.setCurrentIndex(st.repro.time.findData("none"))                          # the day-0 session, not the later repeats
    assert st.repro.study.sensitivity.keys() == {"E1", "E2"} and st.repro.kpis[1].value.text() == "2"
    assert st.repro.study.sensitivity["E2"] == pytest.approx(0.95 * st.repro.study.sensitivity["E1"], rel=0.02)
    st.stability.refresh()
    assert st.stability.study.result is not None and st.stability.study.result.tau == pytest.approx(30, rel=0.05)
    assert st.stability.kpis[0].value.text().endswith("%")
    st.calibration.refresh()
    st.calibration.group.setCurrentIndex(st.calibration.group.findText("E1"))
    st.calibration.time.setCurrentIndex(st.calibration.time.findData("none"))
    cs = st.calibration.study
    assert cs.samples and cs.samples[0].conc == pytest.approx(7.0, rel=0.06)
    assert st.calibration.samples_table.rowCount() == 1
    st.selectivity.refresh()
    assert st.selectivity.study.warnings                                                 # no interferents yet: explained, not an error
    s.add_manual_response("Peak height", "µA", 0.5, conc=10.0, conc_unit="µM", condition="ascorbate", group="E1")
    st.selectivity.refresh()
    assert [r.interferent for r in st.selectivity.study.rows] == ["ascorbate"]

    proj = tmp_path / f"platform{SUFFIX}"
    assert a.save_project_to(proj)
    expect = (len(s.responses), cs.sensitivity, cs.lod, dict(s.extras.get("analysis_params", {})))
    b = make_window(tmp_path / "b")
    b.show()
    assert b.open_project_from(proj)
    assert len(b.session.responses) == expect[0] and b.session.extras.get("analysis_params") == expect[3] and expect[3]
    cb = b.study_tab.calibration
    assert cb.group.currentText() == "E1" and cb.options().quantity == "Peak height"                 # study settings restored
    assert cb.study.sensitivity == pytest.approx(expect[1]) and cb.study.lod == pytest.approx(expect[2])
    assert b.analysis_tab.curve is not None and b.analysis_tab.analyzer.key == "peak"
    assert b.session.get_params("peak", b.analysis_tab.curve.uid) == a.session.get_params("peak", b.analysis_tab.curve.uid)
    assert not b.session.dirty


def test_every_workspace_survives_every_theme_and_an_empty_project(qapp, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    for mode in ("dark", "light"):
        win.set_theme(mode)
        for i in range(win.tabs.count()):
            win.tabs.setCurrentIndex(i)
            for _ in range(3):
                qapp.processEvents()
    assert win.study_tab.calibration.study is None and win.analysis_tab.curve is None and win.electrode_tab.analysis is None


# ===================================================================== home and closing a project

def test_home_keeps_the_project_and_leads_back_to_it(qapp, lab, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    assert not win.tb_home.isEnabled() and not win.act_close.isEnabled()                  # nothing to go back to yet
    win.load_paths([lab])
    assert win.stack.currentIndex() == mw.PAGE_WORK and win.tb_home.isEnabled() and win.act_close.isEnabled()
    n_files = len(win.session.files)
    win.tb_home.trigger()
    assert win.stack.currentIndex() == mw.PAGE_WELCOME and not win.welcome.current_card.isHidden()
    assert win.welcome.current_name.text().startswith("Untitled") and len(win.session.files) == n_files
    shot(win, qapp, "24_home_with_project")
    win.welcome.btn_return.click()
    assert win.stack.currentIndex() == mw.PAGE_WORK and len(win.session.files) == n_files
    win.act_home.trigger()                                                                # the menu entry does the same
    assert win.stack.currentIndex() == mw.PAGE_WELCOME
    win.load_paths([lab])                                                                 # adding data from Home goes to the work page
    assert win.stack.currentIndex() == mw.PAGE_WORK and win.welcome.current_card.isHidden()


def test_close_project_asks_first_and_returns_to_the_start_page(qapp, lab, tmp_path, monkeypatch):
    win = make_window(tmp_path / "w")
    win.show()
    assert win.close_project() is False                                                   # nothing open: nothing to do
    win.load_paths([lab])
    win.session.touch()                                                                   # unsaved changes
    monkeypatch.setattr(win, "_ask_save", lambda: mw.CANCEL)
    assert win.close_project() is False and win.session.files and win.stack.currentIndex() == mw.PAGE_WORK
    monkeypatch.setattr(win, "_ask_save", lambda: mw.DISCARD)
    assert win.close_project() is True
    assert not win.session.files and not win.session.responses and not win.session.dirty
    assert win.stack.currentIndex() == mw.PAGE_WELCOME and win.welcome.current_card.isHidden()
    assert not win.tb_home.isEnabled() and not win.act_close.isEnabled()
    assert win.session.project_path is None and "Closed" in win.statusBar().currentMessage()


def test_close_project_can_save_first(qapp, lab, tmp_path, monkeypatch):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([lab])
    target = tmp_path / "keep.echemproj"
    monkeypatch.setattr(win, "save_project_as", lambda: win.save_project_to(target))
    monkeypatch.setattr(win, "_ask_save", lambda: mw.SAVE)
    win.session.touch()
    assert win.close_project() is True and target.exists() and not win.session.files
    assert win.open_project_from(target) and win.session.files and win.stack.currentIndex() == mw.PAGE_WORK
    assert win.act_close.isEnabled()
    # a saved, unchanged project closes without any question
    monkeypatch.setattr(win, "_ask_save", lambda: pytest.fail("asked although nothing changed"))
    assert win.close_project() is True
