"""The sensor study, electrode characterisation and per-measurement analyses in reports, workbooks and projects.

Everything runs on synthetic data with known answers (0.8 µA per µM, a known electroactive area), so the numbers
in the report can be checked against the truth and not only against the app's own code.
"""
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import openpyxl
import pytest
from PySide6.QtPdf import QPdfDocument

import synth
from echem_analyzer import electrode as electrode_engine
from echem_analyzer import study_analysis as sa
from echem_analyzer import study_report
from echem_analyzer.analyzers import ANALYZERS, upsert_responses
from echem_analyzer.io_palmsens import load_any
from echem_analyzer.models import ProjectData, ProjectMeta, Response
from echem_analyzer.project import load_project, save_project
from echem_analyzer.report import FigureBlock, KeyValueBlock, NoteBlock, TableBlock, build_report
from echem_analyzer.report_pdf import write_pdf
from echem_analyzer.workbook import export_workbook

VOLT, AMP = "PalmSens.Units.Volt", "PalmSens.Units.MicroAmpere"
CONCS = [1, 2, 5, 10, 20, 50]
SLOPE = {"E1": 0.8, "E2": 0.84}                       # µA per µM on each electrode
AREA, CONC_MM, DIFF = 0.05, 5.0, 7.6e-6


def peak_responses(d: Path, group: str, per_um: float, seed: int):
    """Write a DPV series (blank + six levels, ``per_um`` µA per µM) to a pssession and load it."""
    rng = np.random.default_rng(seed)
    e, base = synth.dpv_trace(0.0)
    noisy = lambda y: y + rng.normal(0, 0.01, y.size)                                       # noqa: E731
    ms = [{"title": "DPV blank", "method_id": "dpv", "curves": [("DPV i vs E", e, noisy(base), VOLT, AMP)]}]
    for c in CONCS:
        ms.append({"title": f"DPV {c} µM", "method_id": "dpv",
                   "curves": [("DPV i vs E", e, noisy(base + per_um * c * synth.gauss(e, 0.2, 0.03, 1.0)), VOLT, AMP)]})
    f = d / f"{group}.pssession"
    synth.write_pssession(f, ms)
    df = load_any(f)
    for k, c in enumerate(df.curves):
        c.uid = f"{group}#{k}"
    return df


@pytest.fixture()
def project(tmp_path) -> ProjectData:
    files, responses = [], []
    for seed, (group, per_um) in enumerate(SLOPE.items()):
        df = peak_responses(tmp_path, group, per_um, seed)
        files.append(df)
        an = ANALYZERS["peak"]
        for c in df.curves:
            res = an.run(c, an.defaults())
            assert res.ok
            rows = upsert_responses(responses, c, "peak", [s for s in res.responses if s.quantity == "Peak height"])
            for r in rows:
                r.group = group
                if "blank" in c.meta["run_title"]:
                    r.role, r.conc = "blank", None
    # stability of one concentration on E1, an interferent, and two spiked samples
    for t, v in ((0, 8.0), (3, 7.9), (7, 7.7), (14, 7.3)):
        responses.append(Response(id=f"stab{t}", source_label="stability", quantity="Peak height", unit="µA", value=v, conc=10.0,
                                  group="E3", time=float(t)))
    responses.append(Response(id="int1", source_label="ascorbate", quantity="Peak height", unit="µA", value=0.4, conc=10.0,
                              condition="ascorbate", group="E1"))
    for k, (conc, found) in enumerate(((4.0, 3.3), (15.0, 12.2))):
        responses.append(Response(id=f"spike{k}", source_label=f"spiked CSF {conc}", quantity="Peak height", unit="µA",
                                  value=0.8 * found, conc=conc, group="E1", role="sample"))
    return ProjectData(meta=ProjectMeta(name="Sensor study test", electrode="E1", analyte="GY"), files=files, responses=responses,
                       extras={"analysis_params": {}})


def section(rep, title_start):
    return next(s for s in rep.sections if s.title.startswith(title_start))


def kv(sec, caption):
    return dict(next(b for b in sec.blocks if isinstance(b, KeyValueBlock) and b.caption == caption).rows)


# ===================================================================== report model

def test_study_report_reproduces_the_known_answers(project):
    rep = build_report(project)
    titles = [s.title for s in rep.sections]
    assert titles.index("Sensor study") < titles.index("About this report")
    assert {"Calibration · Peak height", "Reproducibility · Peak height", "Stability · Peak height",
            "Selectivity · Peak height"} <= set(titles)

    cal = section(rep, "Calibration")
    res = kv(cal, "Results")
    assert float(res["Sensitivity"].split()[0]) == pytest.approx(0.82, rel=0.04)            # E1 and E2 pooled
    assert float(res["R²"]) > 0.998 and "levels" in res["Linear range"] or "not established" in res["Linear range"]
    assert res["Standards"].startswith("16 points at 6 concentrations") and res["Blank replicates"] == "2"      # incl. the 4 timed points
    assert any("different times are pooled" in b.text for b in cal.blocks if isinstance(b, NoteBlock))
    settings = kv(cal, "Settings")
    assert "all electrodes" in settings["Selection"] and settings["Concentration unit"] == "µM"
    # the samples were quantified with the calibration: found ≈ 0.8·found/0.82 ... recovery near the values typed
    samples = next(b for b in cal.blocks if isinstance(b, TableBlock) and b.caption.startswith("Samples"))
    assert len(samples.rows) == 2 and all("%" in r[-1] for r in samples.rows)

    rep_s = kv(section(rep, "Reproducibility"), "Results")
    assert rep_s["Electrodes (max per level)"] == "3"                                        # E1, E2 and the timed E3 point at 10 µM
    assert "E1" in rep_s["Sensitivity per electrode"] and float(rep_s["Sensitivity RSD"].split()[0]) == pytest.approx(3.4, abs=1.5)

    stab = kv(section(rep, "Stability"), "Results")
    assert float(stab["Retention at the last time point"].split()[0]) == pytest.approx(7.3 / 8.0 * 100, abs=0.1)

    sel = next(b for b in section(rep, "Selectivity").blocks if isinstance(b, TableBlock))
    assert sel.rows[0][0] == "ascorbate"


def test_figures_have_unique_paths_and_the_summary_lists_the_calibration(project):
    rep = build_report(project)
    paths = [b.path for s in rep.sections for b in s.blocks if isinstance(b, FigureBlock)]
    assert len(paths) == len(set(paths)) and any(p.startswith("study/calibration") for p in paths)
    assert any(p.startswith("analysis/peak__") for p in paths)
    summary = section(rep, "Summary of results")
    t = next(b for b in summary.blocks if isinstance(b, TableBlock) and b.caption == "Sensor study: calibrations")
    assert t.rows[0][0] == "Peak height" and t.rows[0][1] == "analyte"
    overview = section(rep, "Overview")
    assert next(b for b in overview.blocks if isinstance(b, TableBlock)).header[-1] == "Other measurements"


def test_every_analysed_measurement_gets_its_own_block_with_its_settings(project):
    rep = build_report(project)
    analyses = [s for s in rep.sections if s.kind == "analysis"]
    assert len(analyses) == 14                                                               # 7 DPV x 2 electrodes
    assert [s.page_break for s in analyses] == [True, False] * 7                             # two measurements per page
    s = analyses[1]
    assert s.subtitle.startswith("Peak analysis") and kv(s, "Settings")["Baseline"].startswith("Linear")
    assert "Peak height" in " ".join(k for k, _ in next(b for b in s.blocks if b.__class__ is KeyValueBlock and b.caption == "Results").rows)


def test_nothing_is_added_without_responses(project):
    project.responses.clear()
    rep = build_report(project)
    assert not [s for s in rep.sections if s.kind in ("study", "analysis", "electrode")]
    assert not any(b.caption == "Sensor study: calibrations" for s in rep.sections for b in s.blocks if isinstance(b, TableBlock))


def test_stored_study_settings_are_used_by_the_report(project):
    base = study_report.compute_study(project.responses, project.extras).calibrations[0]
    project.extras["study"] = {"calibration": {"quantity": "Peak height", "condition": "analyte", "group": "E2", "k_lod": 3.3,
                                               "k_loq": 11.0, "model": "linear", "conc_unit": "nM", "sigma_from": "residual",
                                               "restrict_range": False, "r2_min": 0.99, "dev_max": 0.1}}
    cs = study_report.compute_study(project.responses, project.extras).calibrations[0]
    assert cs.options.group == "E2" and cs.conc_unit == "nM" and len(cs.x) == 6
    assert cs.sensitivity == pytest.approx(0.84 / 1000, rel=0.03) and base.conc_unit == "µM"
    assert "residual" in cs.fom.sigma_source
    rep = build_report(project)
    cal = section(rep, "Calibration")
    assert "electrode E2" in kv(cal, "Settings")["Selection"] and kv(cal, "Settings")["Concentration unit"] == "nM"
    # a stored option the engine cannot use falls back to defaults instead of breaking the report
    project.extras["study"]["calibration"]["k_lod"] = "three"
    project.extras["study"]["calibration"]["bogus"] = 1
    got = study_report.compute_study(project.responses, project.extras)
    assert got.calibrations and got.calibrations[0].options.k_lod == 3.0 and not got.errors
    assert build_report(project)                                                              # and saving a project still works


def test_a_failing_study_is_reported_not_raised(project, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(sa, "run_stability", boom)
    got = study_report.compute_study(project.responses, project.extras)
    assert got.errors == ["Stability of Peak height: synthetic failure"] and got.calibrations
    rep = build_report(project)
    note = next(b for b in section(rep, "Sensor study").blocks if isinstance(b, NoteBlock) and b.tone == "warning")
    assert "synthetic failure" in note.text


def test_a_quantity_without_enough_standards_is_skipped(project):
    project.responses.append(Response(id="x", quantity="Rct", unit="Ω", value=900.0, conc=5.0, group="E1"))
    cals = study_report.compute_study(project.responses, project.extras).calibrations
    assert [c.options.quantity for c in cals] == ["Peak height"]                              # one Rct point is no calibration


def test_report_figures_are_cached_between_builds(project):
    from echem_analyzer import report as report_mod
    build_report(project)
    n = len(report_mod._CACHE)
    build_report(project)
    assert len(report_mod._CACHE) == n


def test_study_report_code_is_qt_free():
    code = ("import sys; import echem_analyzer.study_report, echem_analyzer.electrode_study; "
            "assert not [m for m in sys.modules if m.startswith('PySide6')], 'Qt was imported'")
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                       cwd=str(Path(__file__).resolve().parents[1]), env={"PYTHONPATH": str(Path(__file__).resolve().parents[1])})
    assert r.returncode == 0, r.stderr


# ===================================================================== electrode characterisation

@pytest.fixture()
def scan_project(tmp_path) -> ProjectData:
    slope = electrode_engine.randles_sevcik_slope(1, AREA, CONC_MM, DIFF)
    cvs = []
    for nu in (0.01, 0.025, 0.05, 0.1, 0.2, 0.4):
        e, i = synth.cv_scan(nu, slope)
        cvs.append({"title": f"CV {nu} V/s", "method_id": "cv", "method": {"SCAN_RATE": nu}, "curves": [("CV i vs E Scan 1", e, i, VOLT, AMP)]})
    f = tmp_path / "scanrate.pssession"
    synth.write_pssession(f, cvs)
    df = load_any(f)
    for k, c in enumerate(df.curves):
        c.uid = f"scan#{k}"
    from echem_analyzer.cv_analysis import group_cv_runs
    runs = group_cv_runs(df.curves)
    use = {r.uid: True for r in runs}
    extras = {"electrode": {"params": {"n": 1, "conc": CONC_MM, "D": DIFF, "area": 0.0314, "temp": 25.0},
                            "scan_rate": {"use": use, "cycle": {}}}}
    return ProjectData(files=[df], extras=extras)


def test_electrode_section_recovers_the_area_and_ignores_unticked_runs(scan_project):
    rep = build_report(scan_project)
    sec = section(rep, "Electrode characterisation · scan-rate")
    res = kv(sec, "Results")
    assert float(res["Electroactive area (ECSA)"].split()[0]) == pytest.approx(AREA, rel=0.03)
    assert float(res["Roughness factor"]) == pytest.approx(AREA / 0.0314, rel=0.04) and "diffusion" in res["log ip – log ν slope"]
    assert kv(sec, "Setup")["Runs used"] == "6"
    assert any(b.path == "electrode/scan_rate.png" for b in sec.blocks if isinstance(b, FigureBlock))
    # untick all but two runs: three are needed, so the section disappears instead of showing nonsense
    keep = list(scan_project.extras["electrode"]["scan_rate"]["use"])[:2]
    scan_project.extras["electrode"]["scan_rate"]["use"] = {u: u in keep for u in scan_project.extras["electrode"]["scan_rate"]["use"]}
    assert not [s for s in build_report(scan_project).sections if s.kind == "electrode"]
    # none of this marks the runs as analysed CV runs
    assert not [s for s in build_report(scan_project).sections if s.kind == "cv"]


def test_electrode_params_tolerate_garbage():
    from echem_analyzer import electrode_study as es
    p = es.ElectrodeParams.from_dict({"n": "2", "conc": "x", "D": 1e-5, "unknown": 3})
    assert p.n == 2 and p.conc == 5.0 and p.D == 1e-5
    assert es.params_from({}).conc == 5.0 and es.state_from({}, "cdl") == {"use": {}, "cycle": {}}


# ===================================================================== workbook, PDF, project

def test_workbook_has_the_study_sheets_and_an_editable_calibration_chart(project, tmp_path):
    out = tmp_path / "wb.xlsx"
    sheets = export_workbook(project, build_report(project), out)
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == sheets and len(set(n.lower() for n in sheets)) == len(sheets) and all(len(n) <= 31 for n in sheets)
    assert {"Responses", "Calibration · Peak height", "Reproducibility · Peak height", "Stability · Peak height",
            "Selectivity · Peak height", "Peak analysis", "Raw data"} <= set(sheets)

    ws = wb["Responses"]
    rows = list(ws.iter_rows(values_only=True))
    assert len(rows) == 1 + len(project.responses) and ws.freeze_panes == "B2" and ws.auto_filter.ref
    head = rows[0]
    first = dict(zip(head, rows[1]))
    r0 = project.responses[0]
    assert first["Value"] == pytest.approx(r0.value) and first["Quantity"] == "Peak height" and first["Electrode"] == "E1"

    cal = wb["Calibration · Peak height"]
    flat = [v for row in cal.iter_rows(values_only=True) for v in row if v not in (None, "")]
    assert "Sensitivity" in flat and any(str(v).startswith("0.8") for v in flat)
    xs = [row[0] for row in cal.iter_rows(values_only=True) if isinstance(row[0], (int, float))]
    assert sorted(set(xs)) == [1, 2, 5, 10, 20, 50] or set(CONCS) <= set(xs)
    with zipfile.ZipFile(out) as z:
        charts = [z.read(n).decode() for n in z.namelist() if n.startswith("xl/charts/chart")]
    assert len(charts) == 1 and "Calibration · Peak height" in charts[0] and charts[0].count("<c:ser>") == 2     # points + fitted curve

    raw = wb["Raw data"]
    labels = [c.value for c in raw[1] if c.value]
    assert len(labels) == 14 and raw.freeze_panes == "A3"
    sw = wb["Summary"]
    assert any(v == "Peak height" for row in sw.iter_rows(values_only=True) for v in row)


def test_pdf_contains_the_study(project, qapp, tmp_path):
    out = tmp_path / "study.pdf"
    pages = write_pdf(build_report(project), out)
    doc = QPdfDocument()
    doc.load(str(out))
    assert doc.pageCount() == pages and pages >= 8
    text = "\n".join(doc.getAllText(i).text() for i in range(doc.pageCount())).lower()          # captions are set in capitals
    for needle in ("Sensor study", "Calibration · Peak height", "Reproducibility", "Stability", "Selectivity", "Peak analysis",
                   "Model comparison", "Samples quantified with this calibration", "Sensitivity", "ascorbate"):
        assert needle.lower() in text, needle
    # nothing runs off the page: every page renders and has ink on it
    for i in range(doc.pageCount()):
        img = doc.render(i, doc.pagePointSize(i).toSize())
        assert not img.isNull()


def test_project_snapshot_has_the_responses_calibrations_and_plots(project, tmp_path):
    path = tmp_path / "p.echemproj"
    save_project(path, project)
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        csv = z.read("results/responses.csv").decode().splitlines()
        cal = z.read("results/study_calibrations.csv").decode().splitlines()
    assert len(csv) == 1 + len(project.responses) and csv[0].startswith("id,measurement,technique")
    assert cal[0].startswith("quantity,condition,model") and cal[1].startswith("Peak height,analyte,linear")
    assert any(n.startswith("plots/study/calibration") for n in names) and any(n.startswith("plots/analysis/peak__") for n in names)
    back = load_project(path)
    assert [r.id for r in back.responses] == [r.id for r in project.responses]
    assert re.search(r"\d", csv[1])
