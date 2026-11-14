"""Responses as the change from each electrode's own blank (label-free and signal-off sensors)."""
import numpy as np
import pytest

from echem_analyzer import study_analysis as sa
from echem_analyzer.models import Response

BASELINES = {"E1": 500.0, "E2": 800.0, "E3": 1200.0}               # Rct0 (Ω) of three electrodes of the same design
CONCS = [1, 2, 5, 10, 20.0]


def impedimetric(baselines=BASELINES, blanks_for=None, slope=0.04, samples=()):
    """Rct rises by 4 % of the electrode's own baseline per µM, whatever the baseline."""
    rs, k = [], 0
    for g, base in baselines.items():
        for c in CONCS:
            rs.append(Response(id=f"s{k}", source_label=f"{g} {c:g} µM", quantity="Rct", unit="Ω", value=base * (1 + slope * c), conc=c,
                               conc_unit="µM", group=g, role="standard"))
            k += 1
        if blanks_for is None or g in blanks_for:
            for j, jitter in enumerate((-1.0, 0.0, 1.0)):
                rs.append(Response(id=f"b{k}", quantity="Rct", unit="Ω", value=base + jitter, group=g, role="blank"))
                k += 1
    for g, c in samples:
        rs.append(Response(id=f"u{k}", source_label=f"unknown on {g}", quantity="Rct", unit="Ω", value=baselines[g] * (1 + 0.04 * c), group=g, role="sample"))
        k += 1
    return rs


def run(rs, mode, **kw):
    return sa.run_calibration(rs, sa.CalibrationOptions(quantity="Rct", model="linear", normalise=mode, **kw))


def test_pooling_electrodes_with_different_baselines_only_works_after_normalising():
    raw = run(impedimetric(), "none")
    assert raw.fit.r2 < 0.95 and raw.y_unit == "Ω"                         # three baselines, one line: a poor fit
    pct = run(impedimetric(), "percent")
    assert pct.fit.r2 == pytest.approx(1.0, abs=1e-9) and pct.y_unit == "%"
    assert pct.sensitivity == pytest.approx(4.0) and pct.sensitivity_unit == "%/µM"                # 4 % per µM, for every electrode
    delta = run(impedimetric(), "delta")
    assert delta.y_unit == "ΔΩ" and delta.fit.r2 < pct.fit.r2              # the absolute change still depends on the baseline
    assert np.allclose(sorted(set(np.round(delta.y[delta.x == 10], 6))), [0.4 * b for b in sorted(BASELINES.values())])


def test_each_electrode_alone_gives_the_same_relative_sensitivity():
    for g in BASELINES:
        cs = run(impedimetric(), "percent", group=g)
        assert cs.sensitivity == pytest.approx(4.0) and cs.fit.r2 == pytest.approx(1.0, abs=1e-9)


def test_unknowns_are_read_in_the_same_normalised_scale():
    cs = run(impedimetric(samples=[("E1", 8.0), ("E3", 3.0)]), "percent")
    s1, s3 = cs.samples
    assert s1.response == pytest.approx(32.0) and s1.conc == pytest.approx(8.0)       # 4 %/µM x 8 µM, from a 660 Ω electrode
    assert s3.conc == pytest.approx(3.0)                                              # the same calibration serves the 1200 Ω one
    assert s1.lo == pytest.approx(8.0) and s1.hi == pytest.approx(8.0)                  # noise-free standards: the interval has no width


def test_blank_scatter_is_expressed_in_the_normalised_unit():
    cs = run(impedimetric(), "percent")
    assert len(cs.blanks) == 9 and cs.blanks.mean() == pytest.approx(0.0, abs=1e-9)     # each blank is measured against its own mean
    assert cs.fom.sigma_source.startswith("blank") and cs.fom.sigma == pytest.approx(np.std(cs.blanks, ddof=1))
    assert cs.lod == pytest.approx(3 * cs.fom.sigma / 4.0)                              # LOD in µM from a σ in %


def test_an_electrode_without_a_blank_borrows_the_others_and_says_so():
    cs = run(impedimetric(blanks_for={"E1", "E2"}), "percent")
    assert any("No blank for electrode “E3”" in w for w in cs.warnings)
    pooled = (500 + 800) / 2
    e3_at_10 = [y for x, y, g in zip(cs.x, cs.y, cs.groups) if g == "E3" and x == 10][0]
    assert e3_at_10 == pytest.approx(100 * (1200 * 1.4 - pooled) / pooled)


def test_with_no_blanks_at_all_the_responses_stay_as_measured():
    cs = run([r for r in impedimetric() if r.role != "blank"], "percent")
    assert cs.y_unit == "Ω" and any("needs blank measurements" in w for w in cs.warnings)


def test_a_blank_of_zero_cannot_be_a_percent_reference():
    rs = impedimetric()
    for r in rs:
        if r.role == "blank" and r.group == "E1":
            r.value = 0.0
    cs = run(rs, "percent")
    assert cs.y_unit == "Ω" and any("blank of zero" in w for w in cs.warnings)
    assert run(rs, "delta").y_unit == "ΔΩ"                                              # a difference does not need a non-zero blank


def test_the_choice_is_stored_with_the_project_options():
    from dataclasses import asdict
    assert asdict(sa.CalibrationOptions())["normalise"] == "none"
    assert set(sa.NORMALISATIONS) == {"none", "delta", "percent"}


def test_the_calibration_panel_offers_it_remembers_it_and_the_report_states_it(qapp, tmp_path):
    from helpers import make_window
    from echem_analyzer import study_report
    win = make_window(tmp_path / "w")
    win.session.responses[:] = impedimetric()
    win.session.responses_changed.emit()
    panel = win.study_tab.calibration
    assert [panel.normalise.itemData(i) for i in range(panel.normalise.count())] == ["none", "delta", "percent"]
    panel.model.setCurrentIndex(panel.model.findData("linear"))
    raw_r2 = panel.study.fit.r2
    panel.normalise.setCurrentIndex(panel.normalise.findData("percent"))
    assert panel.study.y_unit == "%" and panel.study.fit.r2 > raw_r2 and panel.study.sensitivity == pytest.approx(4.0)
    assert win.session.extras["study"]["calibration"]["normalise"] == "percent"            # saved with the project
    settings, _ = study_report.calibration_rows(panel.study)
    assert ("Response", "Percent change from the blank (%), per electrode") in settings
    win.session.responses_changed.emit()                                                   # a refresh restores the choice
    assert panel.normalise.currentData() == "percent"
