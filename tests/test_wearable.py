"""Response and recovery times, drift correction and temperature: what a sensor worn on skin needs."""
import math

import numpy as np
import pytest

from echem_analyzer import analyzers, transients
from echem_analyzer.models import Curve

TAU = 2.0


def first_order(t, t_step, y0, y1, tau=TAU):
    return np.where(t < t_step, y0, y0 + (y1 - y0) * (1 - np.exp(-(t - t_step) / tau)))


# ===================================================================== T95 and recovery

def test_t95_of_a_first_order_response_is_three_time_constants():
    t = np.arange(0, 60, 0.01)
    r = transients.response_time(t, first_order(t, 10.0, 1.0, 5.0), t_step=10.0, pre=5.0, post_window=10.0, t_end=59.0)
    assert r.t_95 == pytest.approx(-TAU * math.log(0.05), rel=0.03)                   # 2.996 τ
    assert r.t_95 > r.t_high > r.t_low > 0
    assert r.t_high == pytest.approx(-TAU * math.log(0.1), rel=0.03)


def test_the_time_to_recover_is_the_same_measurement_on_the_falling_step():
    t = np.arange(0, 60, 0.01)
    rise = transients.response_time(t, first_order(t, 10.0, 1.0, 5.0), 10.0, pre=5.0, post_window=10.0, t_end=59.0)
    fall = transients.response_time(t, first_order(t, 10.0, 5.0, 1.0), 10.0, pre=5.0, post_window=10.0, t_end=59.0)
    assert fall.t_high == pytest.approx(rise.t_high, rel=0.02) and fall.t_95 == pytest.approx(rise.t_95, rel=0.02)
    assert fall.y_start > fall.y_end                                                    # it really did fall


def test_one_noisy_sample_is_not_mistaken_for_the_response_arriving():
    t = np.arange(0, 60, 0.05)
    y = first_order(t, 10.0, 1.0, 5.0) + np.random.default_rng(2).normal(0, 0.02, t.size)
    clean = transients.response_time(t, y, 10.0, pre=5.0, post_window=10.0, t_end=59.0)
    k = int(np.searchsorted(t, 10.5))
    y[k] = 5.2                                                                          # a glitch to the final level half a second after the step
    glitched = transients.response_time(t, y, 10.0, pre=5.0, post_window=10.0, t_end=59.0)
    assert glitched.t_95 == pytest.approx(clean.t_95, abs=0.3) and glitched.t_95 > 4.0
    assert glitched.t_high == pytest.approx(clean.t_high, abs=0.3)


# ===================================================================== the staircase analyzer

def up_and_down(tau=0.8, seed=4):
    """Baseline 0 -> +1 -> +2 (the analyte is added) -> +1 -> 0 (it is washed out), each change a first-order response."""
    t = np.arange(0, 120, 0.05)
    y = np.zeros_like(t)
    for ts, change in ((20, 1.0), (45, 1.0), (70, -1.0), (95, -1.0)):
        m = t >= ts
        y[m] += change * (1 - np.exp(-(t[m] - ts) / tau))
    return t, y + np.random.default_rng(seed).normal(0, 0.003, t.size)


def staircase(t, y, **extra):
    c = Curve(kind="CA", title="additions", x=t, y=y, x_unit="s", y_unit="µA", uid="u")
    a = analyzers.ANALYZERS["staircase"]
    return a.run(c, a.params_for({"drop_last": 0, "window": 5.0, "sharp": "slow", **extra}))


def test_by_default_a_jump_the_other_way_is_not_an_addition_and_is_only_reported():
    t, y = up_and_down()
    r = staircase(t, y)
    assert r.ok and r.metrics[0].value == "2"                                             # the two washes are left out
    assert not any(s.quantity.startswith("Recovery") for s in r.responses)
    assert any("opposite" in w.lower() or "not additions" in w.lower() or "jump" in w.lower() for w in r.warnings)


def test_steps_against_the_first_one_are_recoveries_with_their_own_times():
    t, y = up_and_down(tau=0.8)
    r = staircase(t, y, opposite="recovery")
    assert r.ok and r.metrics[0].value == "4"
    header, rows = r.table
    assert header[-2:] == ["t90 (s)", "t95 (s)"]
    assert [row[0] for row in rows] == ["0 (baseline)", "1", "2", "3 (recovery)", "4 (recovery)"]
    by_quantity = {}
    for s in r.responses:
        by_quantity.setdefault(s.quantity, []).append(s.value)
    assert set(by_quantity) >= {"t90", "t95", "Recovery t90", "Recovery t95", "Plateau", "Step change (Δ)"}
    assert len(by_quantity["t90"]) == len(by_quantity["Recovery t90"]) == 2
    assert by_quantity["t90"] == pytest.approx([-0.8 * math.log(0.1)] * 2, rel=0.3)
    assert by_quantity["t95"] == pytest.approx([-0.8 * math.log(0.05)] * 2, rel=0.3)
    assert by_quantity["Recovery t90"] == pytest.approx(by_quantity["t90"], rel=0.3)
    assert by_quantity["Recovery t95"] == pytest.approx(by_quantity["t95"], rel=0.3)
    assert all(a < b for a, b in zip(by_quantity["t90"], by_quantity["t95"]))


def test_a_staircase_of_additions_alone_has_no_recoveries():
    from test_study import slow_staircase
    t, y = slow_staircase(0.8)
    r = staircase(t, y)
    assert not any(s.quantity.startswith("Recovery") for s in r.responses)
    assert all(not row[0].endswith("(recovery)") for row in r.table[1])
    assert sum(s.quantity == "t95" for s in r.responses) == 3


# ===================================================================== baseline drift

def drifting(rate_per_min=0.6, tau=0.8, seed=6):
    """The additions staircase of test_study (true changes -0.5, -0.9 and -1.2 against the baseline) on a baseline that drifts."""
    from test_study import slow_staircase
    t, y = slow_staircase(tau, seed)
    return t, y + rate_per_min / 60.0 * t


def deltas(result):
    return [s.value for s in result.responses if s.quantity == "Step change (Δ)"]


def test_a_straight_line_drift_is_removed_so_the_plateaus_show_what_the_analyte_did():
    t, y = drifting(0.6)                                                              # +0.6 µA/min: 1 µA over the run, as big as the signal
    raw = staircase(t, y)
    fixed = staircase(t, y, detrend="linear")
    assert deltas(raw) != pytest.approx([-0.5, -0.9, -1.2], abs=0.1)                  # the drift is in the steps
    assert deltas(fixed) == pytest.approx([-0.5, -0.9, -1.2], abs=0.04)
    plateaus = [s.value for s in fixed.responses if s.quantity == "Plateau"]
    assert plateaus == pytest.approx([-0.3, -0.8, -1.2, -1.5], abs=0.05)
    row = next(r for r in fixed.rows if r[0] == "Baseline drift removed")
    assert float(row[1]) == pytest.approx(0.6, rel=0.1) and row[2] == "µA/min"


def test_the_corrected_trace_is_what_is_drawn_and_the_original_is_untouched():
    t, y = drifting(0.6)
    c = Curve(kind="CA", title="additions", x=t, y=y.copy(), x_unit="s", y_unit="µA", uid="u")
    a = analyzers.ANALYZERS["staircase"]
    r = a.run(c, a.params_for({"drop_last": 0, "window": 5.0, "sharp": "slow", "detrend": "linear"}))
    shown = r.data[0]
    assert np.array_equal(c.y, y) and not np.array_equal(shown.y, y) and len(shown.y) == len(y)
    assert shown.y[0] == pytest.approx(y[0])                                          # anchored at the start of the trace
    assert shown.y[-1] - y[-1] == pytest.approx(-0.6 / 60 * (t[-1] - t[0]), rel=0.03)   # the drift estimate is good to a few percent
    none = a.run(c, a.params_for({"drop_last": 0, "window": 5.0, "sharp": "slow"}))
    assert none.data[0] is c


def test_too_little_baseline_leaves_the_trace_alone_and_says_why():
    t, y = drifting(0.6)
    keep = t >= 18.0                                                                  # only 7 s before the first step at 25 s
    c = Curve(kind="CA", title="short", x=t[keep], y=y[keep], x_unit="s", y_unit="µA", uid="u")
    a = analyzers.ANALYZERS["staircase"]
    r = a.run(c, a.params_for({"drop_last": 0, "window": 5.0, "sharp": "slow", "detrend": "linear"}))
    assert r.ok and any("needs at least 10 s" in w for w in r.warnings)
    assert not any(row[0] == "Baseline drift removed" for row in r.rows) and r.data[0] is c


def test_a_drift_free_trace_is_not_changed_by_asking_for_the_correction():
    from test_study import slow_staircase
    t, y = slow_staircase(0.8)
    plain, corrected = staircase(t, y), staircase(t, y, detrend="linear")
    assert deltas(corrected) == pytest.approx(deltas(plain), abs=0.02)
    drift = float(next(r for r in corrected.rows if r[0] == "Baseline drift removed")[1])
    assert abs(drift) < 0.05                                                          # µA/min: nothing to remove


# ===================================================================== temperature

def temp_responses(temps, alpha=2.0, y25=100.0, conc=10.0, noise=0.0, seed=1, group="E1", reps=1, conc_unit="µM", quantity="Signal"):
    """A signal that changes by `alpha` % of its 25 °C value per °C."""
    from echem_analyzer.models import Response
    rng = np.random.default_rng(seed)
    rs = []
    for i, t_c in enumerate(temps):
        for j in range(reps):
            rs.append(Response(id=f"t{i}_{j}", source_label=f"{t_c:g} °C #{j + 1}", quantity=quantity, unit="nA", conc=conc, conc_unit=conc_unit,
                               value=float(y25 * (1 + alpha / 100 * (t_c - 25.0)) + rng.normal(0, noise)), group=group, role="standard", temp_c=float(t_c)))
    return rs


def run_t(rs, **kw):
    from echem_analyzer import study_analysis as sa
    return sa.run_temperature(rs, "Signal", **kw)


def test_the_temperature_coefficient_is_recovered_and_compensation_removes_it():
    ts = run_t(temp_responses([15, 20, 25, 30, 35], alpha=2.0))
    assert ts.ok and ts.alpha == pytest.approx(2.0) and ts.y_ref == pytest.approx(100.0) and ts.r2 == pytest.approx(1.0)
    assert ts.slope == pytest.approx(2.0)                                                # nA per °C
    assert ts.rsd_raw > 10.0 and ts.rsd_compensated == pytest.approx(0.0, abs=1e-9)        # 2 %/°C over ±10 °C is a ±20 % swing
    assert [row.temp for row in ts.rows] == [15, 20, 25, 30, 35]
    assert all(row.compensated_mean == pytest.approx(100.0) for row in ts.rows)
    assert ts.warnings == []


def test_a_negative_coefficient_and_another_reference_temperature():
    cold = run_t(temp_responses([10, 15, 20, 25, 30], alpha=-1.5, y25=40.0))
    assert cold.alpha == pytest.approx(-1.5) and cold.y_ref == pytest.approx(40.0)
    at37 = run_t(temp_responses([30, 33, 36, 39, 42], alpha=2.0), ref_temp=37.0)
    y37 = 100.0 * (1 + 0.02 * 12)
    assert at37.y_ref == pytest.approx(y37) and at37.alpha == pytest.approx(2.0 * 100.0 / y37)         # the same slope, a larger reference value
    assert all(row.compensated_mean == pytest.approx(y37) for row in at37.rows)                         # everything referred to 37 °C


def test_the_coefficients_uncertainty_is_honest():
    estimates, covered = [], 0
    for seed in range(300):
        ts = run_t(temp_responses([20, 25, 30, 35, 40], alpha=2.0, noise=2.0, reps=2, seed=seed))
        estimates.append(ts.alpha)
        covered += abs(ts.alpha - 2.0) <= 2.0 * ts.alpha_se                                              # about a 95 % interval
    assert np.mean(estimates) == pytest.approx(2.0, abs=0.05)
    assert covered / 300 > 0.90


def test_replicates_are_counted_per_temperature():
    ts = run_t(temp_responses([20, 30, 40], alpha=1.0, reps=3, noise=0.3))
    assert [row.n for row in ts.rows] == [3, 3, 3] and all(row.sd > 0 for row in ts.rows)
    assert len(ts.t) == 9 and len(ts.y_compensated) == 9


def test_one_concentration_at_a_time_and_a_warning_when_several_are_pooled():
    from echem_analyzer.models import Response
    low = temp_responses([15, 25, 35], alpha=2.0, y25=50.0, conc=5.0)
    high = temp_responses([15, 25, 35], alpha=2.0, y25=200.0, conc=20.0)
    for r in high:
        r.id += "h"
    both = low + high
    pooled = run_t(both)
    assert any("several concentrations are pooled" in w for w in pooled.warnings)
    only_low = run_t(both, conc=5.0)
    assert only_low.alpha == pytest.approx(2.0) and only_low.y_ref == pytest.approx(50.0) and len(only_low.t) == 3
    assert not any("pooled" in w for w in only_low.warnings)
    in_mm = run_t(both, conc=0.005, conc_unit="mM")                                       # the same 5 µM, asked for in mM
    assert in_mm.conc_unit == "mM" and len(in_mm.t) == 3 and in_mm.y_ref == pytest.approx(50.0)
    assert run_t(low + [Response(id="x", quantity="Signal", unit="nA", value=1.0, role="standard")]).ok   # a response without a temperature is ignored


def test_what_the_study_warns_about():
    assert any("two or more temperatures" in w for w in run_t(temp_responses([25])).warnings)
    assert any("two or more temperatures" in w for w in run_t(temp_responses([25, 30])).warnings)           # three points at least
    two = run_t(temp_responses([20, 30, 30], alpha=1.0))
    assert any("Only two temperatures" in w for w in two.warnings)
    narrow = run_t(temp_responses([24, 25, 26], alpha=1.0))
    assert any("only 2.0 °C" in w for w in narrow.warnings)
    outside = run_t(temp_responses([30, 35, 40], alpha=1.0))
    assert any("outside the range measured" in w and "extrapolation" in w for w in outside.warnings)
    flat = run_t(temp_responses([15, 20, 25, 30, 35], alpha=0.0, noise=1.5, seed=2))
    assert any("not clearly different from zero" in w for w in flat.warnings)
    zero_ref = run_t(temp_responses([15, 25, 35], alpha=2.0, y25=0.0))
    assert not zero_ref.ok and any("zero" in w and "undefined" in w for w in zero_ref.warnings)
    assert any("several electrodes" in w for w in run_t(temp_responses([15, 25, 35], group="E1") + [
        type(r)(**{**r.__dict__, "id": r.id + "b", "group": "E2"}) for r in temp_responses([15, 25, 35], group="E2")]).warnings)


def test_temperature_survives_saving_and_old_projects_load_without_it(tmp_path):
    import json
    import zipfile
    from echem_analyzer.project import ProjectData, load_project, save_project
    rs = temp_responses([15, 25, 35])
    save_project(tmp_path / "p.echemproj", ProjectData(responses=rs))
    back = load_project(tmp_path / "p.echemproj")
    assert [r.temp_c for r in back.responses] == [15.0, 25.0, 35.0]
    old = tmp_path / "old.echemproj"                                                   # a project written before the field existed
    with zipfile.ZipFile(tmp_path / "p.echemproj") as zin, zipfile.ZipFile(old, "w") as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item.endswith("project.json") or item == "project.json":
                doc = json.loads(data)
                for r in doc.get("responses", []):
                    r.pop("temp_c", None)
                data = json.dumps(doc).encode()
            zout.writestr(item, data)
    assert [r.temp_c for r in load_project(old).responses] == [None, None, None]


# ===================================================================== temperature in the Study tab and the report

def test_the_temperature_panel_shows_the_coefficient_and_remembers_its_settings(qapp, tmp_path):
    from helpers import make_window
    win = make_window(tmp_path / "w")
    st = win.study_tab
    assert [st.tabs.tabText(i) for i in range(st.tabs.count() - 3, st.tabs.count())] == ["Temperature", "Devices", "Variants"]    # appended: saved tab indices keep their meaning
    win.session.responses[:] = temp_responses([15, 20, 25, 30, 35], alpha=2.0, reps=2, noise=0.4)
    win.session.responses_changed.emit()
    panel = st.temperature
    assert [c.label.text() for c in panel.kpis if not c.isHidden()] == ["COEFFICIENT", "AT 25 °C", "R²", "RSD"]
    assert panel.kpis[0].value.text().startswith("+2") and panel.table.rowCount() == 5
    assert panel.study.ok and "→" in panel.kpis[3].value.text()
    panel.ref.setValue(37.0)
    assert panel.study.ref_temp == 37.0 and panel.kpis[1].label.text() == "AT 37 °C"
    assert win.session.extras["study"]["temperature"]["ref_temp"] == 37.0
    panel.refresh()                                                                      # a refresh restores it
    assert panel.ref.value() == 37.0
    win.session.responses[:] = temp_responses([25, 25, 25])                              # one temperature only: nothing to fit
    win.session.responses_changed.emit()
    assert not panel.study.ok and panel.kpis[0].value.text() == "—" and panel.table.rowCount() == 0
    assert "two or more temperatures" in panel.notice.text()


def test_temperatures_are_entered_in_the_responses_table_and_the_dialogs(qapp, tmp_path):
    from PySide6.QtCore import Qt
    from helpers import make_window
    from echem_analyzer.gui import study_tab as stab
    win = make_window(tmp_path / "w")
    win.session.responses[:] = temp_responses([None] * 0 + [25.0, 30.0], alpha=1.0)
    for r in win.session.responses:
        r.temp_c = None
    win.session.responses_changed.emit()
    rp = win.study_tab.responses
    assert rp.table.horizontalHeaderItem(stab.C_TEMP).text() == "T (°C)" and rp.table.columnCount() == 14
    assert rp.table.item(0, stab.C_TEMP).text() == "" and rp.table.item(0, stab.C_TEMP).flags() & Qt.ItemIsEditable
    rp.table.item(0, stab.C_TEMP).setText("27,5")                                       # a decimal comma is accepted
    assert win.session.responses[0].temp_c == 27.5 and rp.table.item(0, stab.C_TEMP).text() == "27.5"
    rp.table.item(0, stab.C_TEMP).setText("")                                            # blank clears it
    assert win.session.responses[0].temp_c is None
    bulk = stab.BulkDialog(2)
    bulk.temp.setText("31")
    assert bulk.changes() == {"temp_c": 31.0}
    manual = stab.ManualDialog()
    manual.temp.setText("36.6")
    assert manual.values()["temp_c"] == 36.6
    manual.temp.setText("warm")
    assert manual.values()["temp_c"] is None


def test_the_report_has_a_temperature_section_when_standards_have_temperatures(tmp_path):
    from echem_analyzer import study_report
    from echem_analyzer.project import ProjectData
    rs = temp_responses([15, 20, 25, 30, 35], alpha=2.0, reps=2, noise=0.4)
    data = ProjectData(responses=rs, extras={"study": {"temperature": {"quantity": "Signal", "ref_temp": 30.0}}})
    sections = study_report.study_sections(data, with_figures=True)
    (sec,) = [s for s in sections if s.title.startswith("Temperature")]
    kv = dict(next(b for b in sec.blocks if isinstance(b, study_report.KeyValueBlock)).rows)
    assert kv["Temperature coefficient"].startswith("+1.82") and "Response at 30 °C" in kv        # 2 nA/°C on 110 nA at 30 °C = 1.82 %/°C
    assert kv["Response at 30 °C"].startswith("110")
    assert any(isinstance(b, study_report.FigureBlock) and b.png.startswith(b"\x89PNG") for b in sec.blocks)
    table = next(b for b in sec.blocks if isinstance(b, study_report.TableBlock))
    assert [row[0] for row in table.rows] == ["15", "20", "25", "30", "35"] and [row[1] for row in table.rows] == ["2"] * 5
    plain = study_report.compute_study([r for r in rs if r.temp_c == 25.0], {})
    assert plain.temperatures == []                                                      # one temperature: no section
    header, rows = study_report.responses_table(rs)
    assert header[-1] == "T (°C)" and rows[0][-1] == "15"
    for r in rs:
        r.temp_c = None
    assert "T (°C)" not in study_report.responses_table(rs)[0]                           # the column appears only when it has something to say
