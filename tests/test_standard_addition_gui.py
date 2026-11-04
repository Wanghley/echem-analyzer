"""The Standard addition tab: unit selectors, the one-correction rule, the results panel, presets and projects."""
import pytest

import synth
from echem_analyzer import presets
from echem_analyzer import standard_addition as sa
from echem_analyzer.models import CAAnalysisSettings
from echem_analyzer.project import SUFFIX
from helpers import make_window
from test_standard_addition import trace


@pytest.fixture()
def tab(qapp, tmp_path):
    t, i, s = trace(40.0, model="spec", bkg=0.3, noise=0.002, seed=11)
    f = tmp_path / "sa.csv"
    synth.write_pstrace_csv(f, [("Chronoamperometry [1]: CA i vs t [1]", t, i, "s", "µA")])
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([f])
    ca = win.ca_tab
    ca.select_curve(win.session.ca_curves()[0].uid)
    for k, v in s.to_dict().items():
        setattr(ca.s, k, v)
    ca._populate_controls()
    ca.recompute()
    return win, ca


def text(ca):
    return ca.results_label.text()


def test_unit_selectors_convert_the_display_and_keep_the_quantity(tab):
    win, ca = tab
    v0 = ca._binds["v0_ml"]
    assert v0.combo.currentData() == "mL" and v0.spin.value() == pytest.approx(10.0)
    v0.combo.setCurrentIndex(v0.combo.findData("µL"))                         # same 10 mL, shown in µL
    assert v0.spin.value() == pytest.approx(10000.0) and ca.s.v0_ml == pytest.approx(10.0) and ca.s.v0_unit == "µL"
    v0.spin.setValue(5000.0)                                                  # typing 5000 µL stores 5 mL
    assert ca.s.v0_ml == pytest.approx(5.0)
    v0.setValue(10.0)                                                         # programmatic API takes the stored unit
    assert v0.spin.value() == pytest.approx(10000.0) and v0.value() == pytest.approx(10.0)

    bkg = ca._binds["background"]
    bkg.combo.setCurrentIndex(bkg.combo.findData("nA"))
    assert bkg.spin.value() == pytest.approx(300.0) and ca.s.background == pytest.approx(0.3) and ca.s.bkg_unit == "nA"
    bkg.spin.setValue(250.0)
    assert ca.s.background == pytest.approx(0.25)

    vadd = ca._binds["v_add_ul"]
    vadd.combo.setCurrentIndex(vadd.combo.findData("mL"))
    assert vadd.spin.value() == pytest.approx(0.02) and ca.s.v_add_ul == pytest.approx(20.0) and ca.s.v_add_unit == "mL"
    # the unit choices are remembered for new runs
    assert win.session.defaults()["v0_unit"] == "µL" and win.session.defaults()["bkg_unit"] == "nA"


def test_results_panel_gives_units_on_every_value_and_all_three_sigmas(tab):
    win, ca = tab
    t = text(ca)
    for needle in ("Slope b = sensitivity", "µA/mM", "Intercept a", "R²", "s(y/x)", "Suitability", "minimum 12",
                   "LOD = 3.3σ/S", "LOQ = 10σ/S", "x-intercept x₀", "C_x = |x₀| in the cell", "mM (95 % CI)",
                   "C_sample = C_x·V₀/V_sample", "residual s(y/x)", "baseline noise", "blank replicates", "Points in fit (N)"):
        assert needle in t, needle
    assert ca.kpi_cx.sub.text().endswith("mM (95 % CI)") and ca._binds["dilution_mode"].itemText(1) == "Signal scaling" and "LOQ" in ca.kpi_lod.sub.text()
    assert ca.result.warnings == [] and "⚠" not in t


def test_warnings_appear_in_the_panel_and_do_not_block(tab):
    win, ca = tab
    ca._binds["background"].setValue(0.0)
    t = text(ca)
    assert "Background current is 0" in t and "⚠" in t
    assert ca.result.fit is not None and "C_x = |x₀| in the cell" in t                  # the calculation still ran


def test_dilution_is_one_choice_and_changes_the_numbers(tab):
    win, ca = tab
    box = ca._binds["dilution_mode"]
    assert [box.itemData(i) for i in range(box.count())] == ["concentration", "signal", "none"]
    before = ca.result.conc_added[-1]
    box.setCurrentIndex(box.findData("signal"))
    assert ca.s.dilution_mode == "signal" and ca.result.conc_added[-1] > before         # no (V0 + ΣV) in the axis
    assert not hasattr(ca.s, "dilution_conc") and not hasattr(ca.s, "dilution_signal")


def test_blank_replicates_set_the_blank_sigma(tab):
    win, ca = tab
    bkg = ca._binds["background"]
    bkg.combo.setCurrentIndex(bkg.combo.findData("nA"))                                 # replicates are typed in the same unit
    ca.blank_edit.setText("10.0, 12.5; 10.8  12.1")
    ca.blank_edit.editingFinished.emit()
    assert ca.s.blank_replicates == pytest.approx([0.0100, 0.0125, 0.0108, 0.0121])
    assert ca.result.sigmas["blank"] == pytest.approx(0.0011, rel=0.1)
    ca._binds["sigma_source"].setCurrentIndex(ca._binds["sigma_source"].findData("blank"))
    assert ca.result.sigma_used == "blank" and "σ (blank replicates)" in text(ca)
    ca.blank_edit.setText("1.0 abc")
    ca.blank_edit.editingFinished.emit()                                                # not numbers: the stored values come back
    assert ca.s.blank_replicates == pytest.approx([0.0100, 0.0125, 0.0108, 0.0121]) and ca.blank_edit.text().startswith("10")


def test_compare_baseline_shows_both_results(tab):
    win, ca = tab
    assert "without the baseline point" not in text(ca)
    ca._binds["compare_baseline"].setChecked(True)
    t = text(ca)
    assert "C_x without the baseline point" in t and ca.result.alt["n"] == ca.result.fit.n - 1
    ca._binds["include_baseline"].setChecked(False)
    assert "C_x with the baseline point" in text(ca)


def test_presets_carry_the_new_settings(tab):
    win, ca = tab
    ca._binds["v0_ml"].combo.setCurrentIndex(ca._binds["v0_ml"].combo.findData("µL"))
    ca._binds["dilution_mode"].setCurrentIndex(ca._binds["dilution_mode"].findData("signal"))
    ca._binds["lod_k"].setValue(3.0)
    ca._binds["suitability_min"].setValue(20.0)
    ca._binds["verified_ul"].setValue(19.5)
    ca._binds["plausible_max"].setValue(5.0)
    win.session.presets.save("ca", "my protocol", presets.ca_preset_from_settings(ca.s))
    fresh = CAAnalysisSettings()
    assert presets.apply_ca_preset(fresh, win.session.presets.get("ca", "my protocol")) == []
    assert (fresh.v0_unit, fresh.dilution_mode, fresh.lod_k, fresh.suitability_min, fresh.verified_ul, fresh.plausible_max) == \
           ("µL", "signal", 3.0, 20.0, 19.5, 5.0)
    ca._binds["dilution_mode"].setCurrentIndex(0)
    ca._binds["v0_ml"].combo.setCurrentIndex(0)
    presets.apply_ca_preset(ca.s, win.session.presets.get("ca", "my protocol"))
    ca._on_preset_applied("run")                                                          # the controls follow the preset
    assert ca._binds["dilution_mode"].currentData() == "signal" and ca._binds["v0_ml"].combo.currentData() == "µL"
    assert ca._binds["v0_ml"].spin.value() == pytest.approx(10000.0) and ca._binds["lod_k"].value() == 3.0


def test_project_round_trip_keeps_every_new_setting(tab, tmp_path):
    win, ca = tab
    ca._binds["dilution_mode"].setCurrentIndex(ca._binds["dilution_mode"].findData("signal"))
    ca._binds["verified_ul"].setValue(19.0)
    ca._binds["lod_k"].setValue(3.0)
    ca._binds["compare_baseline"].setChecked(True)
    ca.blank_edit.setText("0.01 0.012 0.011")
    ca.blank_edit.editingFinished.emit()
    expect = ca.s.to_dict()
    path = tmp_path / f"p{SUFFIX}"
    assert win.save_project_to(path)
    win2 = make_window(tmp_path / "w2")
    assert win2.open_project_from(path)
    got = win2.session.settings[win2.session.ca_curves()[0].uid].to_dict()
    assert got == expect
    c2 = win2.ca_tab
    c2.select_curve(win2.session.ca_curves()[0].uid)
    assert c2.result.sigma_used == "fit" and c2.result.alt is not None and c2.result.volumes_ul[0] == 19.0


def test_report_setup_lists_the_new_inputs(tab):
    win, ca = tab
    from echem_analyzer.report import ca_setup_rows
    rows = dict(ca_setup_rows(ca.s, len(ca.result.step_times)))
    assert rows["Dilution correction"] == sa.DILUTION_LABELS["concentration"] and rows["σ for LOD/LOQ"] == "residual s(y/x)"
    assert rows["LOD / LOQ factors"] == "3.3σ/S and 10σ/S" and rows["Suitability threshold"] == "12"
    assert "Dilution-corrected conc." not in rows and "Signal scaled for dilution" not in rows
