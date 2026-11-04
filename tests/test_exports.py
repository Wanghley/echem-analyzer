"""Plots, report model, PDF, Excel workbook and the plots embedded in a project."""
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import openpyxl
import pytest
from PySide6.QtPdf import QPdfDocument

from echem_analyzer import palette, plots
from echem_analyzer.ca_analysis import analyze_ca, detect_step_events
from echem_analyzer.cv_analysis import analyze_run, group_cv_runs
from echem_analyzer.io_palmsens import load_csv, load_pssession
from echem_analyzer.models import KIND_CA, CAAnalysisSettings, CVSettings, ProjectData, ProjectMeta
from echem_analyzer.project import load_project, save_project
from echem_analyzer.report import (
    FigureBlock, KeyValueBlock, TableBlock, analysed_runs, build_report, clear_figure_cache,
)
from echem_analyzer.report_pdf import write_pdf
from echem_analyzer.workbook import _sheet_name, export_workbook


@pytest.fixture()
def data(data_dir) -> ProjectData:
    files = []
    for fn in ("2026-11-02 B2 GY data.csv", "2026-11-02 B5 CSF C1 data.csv", "2026-11-02 A6 GY data.pssession"):
        f = load_pssession(data_dir / fn) if fn.endswith("pssession") else load_csv(data_dir / fn)
        for k, c in enumerate(f.curves):
            c.uid = f"{f.path}#{k}"
        files.append(f)
    ca, cv = {}, {}
    for f in files:
        for c in f.of_kind(KIND_CA):
            if c.meta.get("run_title") == "calibration":
                continue                                  # a decay: no staircase
            steps, ignored = detect_step_events(c.x[:-1], c.y[:-1])
            ca[c.uid] = CAAnalysisSettings(step_times=[float(t) for t in steps], ignored_events=[float(t) for t in ignored],
                                           stock_conc=505.1, v0_ml=10.0, v_add_ul=10.0)
        for run in group_cv_runs(f.curves):
            cv[run.uid] = CVSettings(scan_rate=0.05, excluded_cycles=[1])
    return ProjectData(meta=ProjectMeta(name="CSF sensor conditioning", operator="W", electrode="B2 / B5", analyte="GY",
                                        matrix="CSF", notes="Placeholder volumes."),
                       files=files, ca_settings=ca, cv_settings=cv)


def png_ok(b: bytes) -> bool:
    return b[:8] == b"\x89PNG\r\n\x1a\n" and plots.png_size(b)[0] > 200


# ===================================================================== plots

@pytest.mark.parametrize("pal", [palette.LIGHT, palette.DARK], ids=["light", "dark"])
def test_every_plot_renders_in_both_palettes(data, pal):
    ca_runs, cv_runs = analysed_runs(data)
    c, s, r = ca_runs[0]
    run, rs = cv_runs[0]
    res = analyze_run(run, rs)
    assert png_ok(plots.render_png(lambda fig, p: plots.ca_trace(fig.add_subplot(111), c, s, r, p, smooth=5), palette=pal))
    assert png_ok(plots.render_png(lambda fig, p: plots.ca_calibration(fig, s, r, p), palette=pal))
    assert png_ok(plots.render_png(lambda fig, p: plots.cv_cycles(fig.add_subplot(111), run, rs, res, p, branches=True), palette=pal))
    assert png_ok(plots.render_png(lambda fig, p: plots.cv_trends(fig, res, rs, p), palette=pal))
    assert png_ok(plots.render_png(lambda fig, p: plots.compare_traces(fig.add_subplot(111), ca_runs, p, True, True, lambda x: x.title), palette=pal))
    assert png_ok(plots.render_png(lambda fig, p: plots.compare_calibrations(fig.add_subplot(111), ca_runs, p, True, lambda x: x.title), palette=pal))
    assert png_ok(plots.render_png(lambda fig, p: plots.cv_overlay(fig.add_subplot(111), list(run.cycles.values()), p), palette=pal))


def test_light_and_dark_figures_differ_but_have_the_same_size(data):
    c, s, r = analysed_runs(data)[0][0]
    draw = lambda fig, p: plots.ca_trace(fig.add_subplot(111), c, s, r, p)           # noqa: E731
    light, dark = plots.render_png(draw, palette=palette.LIGHT), plots.render_png(draw, palette=palette.DARK)
    assert light != dark and plots.png_size(light) == plots.png_size(dark)


def test_plots_handle_degenerate_inputs(data):
    """No fit yet, no cycles selected, no runs ticked: each must still draw (with a hint), not raise."""
    c, s, r = analysed_runs(data)[0][0]
    s2 = CAAnalysisSettings(step_times=list(s.step_times))                                # no stock / volumes
    r2 = analyze_ca(c.x, c.y, s2)
    assert r2.fit is None
    assert png_ok(plots.render_png(lambda fig, p: plots.ca_calibration(fig, s2, r2, p)))
    run, rs = analysed_runs(data)[1][0]
    all_off = CVSettings(excluded_cycles=run.cycle_numbers)
    assert png_ok(plots.render_png(lambda fig, p: plots.cv_cycles(fig.add_subplot(111), run, all_off, [], p)))
    assert png_ok(plots.render_png(lambda fig, p: plots.cv_trends(fig, [], all_off, p)))
    assert png_ok(plots.render_png(lambda fig, p: plots.compare_traces(fig.add_subplot(111), [], p, True, True, str)))


def test_plotting_report_and_workbook_code_is_qt_free():
    """The figure, report, project and workbook code must run without PySide6 (e.g. in a future CLI)."""
    code = ("import sys; import echem_analyzer.plots, echem_analyzer.report, echem_analyzer.project, "
            "echem_analyzer.workbook, echem_analyzer.palette; "
            "bad = [m for m in sys.modules if m.startswith('PySide6')]; "
            "assert not bad, bad")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=str(Path(__file__).resolve().parents[1]))
    assert r.returncode == 0, r.stderr[-600:]


# ===================================================================== report model

def test_report_contents_match_the_analysis(data):
    clear_figure_cache()
    rep = build_report(data)
    assert rep.title == "CSF sensor conditioning" and ("Electrode / sensor", "B2 / B5") in rep.meta_rows
    ca_runs, cv_runs = analysed_runs(data)
    kinds = [s.kind for s in rep.sections]
    assert kinds.count("ca") == len(ca_runs) == 3 and kinds.count("cv") == len(cv_runs) == 3 and "compare" in kinds

    sec = next(s for s in rep.sections if s.kind == "ca" and "B2 GY" in s.title)
    kv = {k: v for b in sec.blocks if isinstance(b, KeyValueBlock) for k, v in b.rows}
    c, s, r = next(x for x in ca_runs if "B2 GY" in x[0].source)
    assert kv["Slope = sensitivity"].startswith(f"{r.fit.slope:.4g}")
    assert kv["Stock concentration"] == "505.1 mM" and kv["Cell volume V₀"] == "10 mL"
    paths = [b.path for b in sec.blocks if isinstance(b, FigureBlock)]
    assert len(paths) == 2 and all(p.endswith(".png") for p in paths) and all(png_ok(b.png) for b in sec.blocks if isinstance(b, FigureBlock))
    # the step tables live in the appendix, one per analysed CA run
    app = next(s for s in rep.sections if s.title.startswith("Appendix"))
    assert len([b for b in app.blocks if isinstance(b, TableBlock)]) == 3

    a6 = next(s for s in rep.sections if s.kind == "ca" and "std addition" in s.title)
    assert any("opposite direction" in getattr(b, "text", "") for b in a6.blocks)       # the warning travels into the report


def test_figures_are_cached_and_invalidated_by_changes(data):
    clear_figure_cache()
    a = build_report(data)
    b = build_report(data)
    fa, fb = a.figures(), b.figures()
    assert all(fa[k].png is fb[k].png for k in fa)                                       # served from the cache
    c, s = next((c, data.ca_settings[c.uid]) for f in data.files for c in f.of_kind(KIND_CA) if c.uid in data.ca_settings)
    s.stock_conc = 100.0                                                                  # a real change
    changed = build_report(data).figures()
    stem = next(k for k in fa if "B2" in k and k.endswith("calibration.png"))
    assert changed[stem].png != fa[stem].png


def test_runs_that_were_never_opened_are_not_reported(data):
    data.ca_settings.clear()
    data.cv_settings.clear()
    rep = build_report(data)
    assert [s.kind for s in rep.sections if s.kind in ("ca", "cv", "compare")] == []
    assert not rep.figures()


# ===================================================================== PDF

def test_pdf_is_a_real_multipage_document_with_the_right_text(data, qapp, tmp_path):
    rep = build_report(data)
    out = tmp_path / "report.pdf"
    pages = write_pdf(rep, out)
    assert out.read_bytes()[:5] == b"%PDF-" and pages >= 5
    doc = QPdfDocument()
    doc.load(str(out))
    assert doc.pageCount() == pages
    text = "\n".join(doc.getAllText(i).text() for i in range(doc.pageCount()))
    c, s, r = next(x for x in analysed_runs(data)[0] if "B2 GY" in x[0].source)
    for needle in ("CSF sensor conditioning", "Electrochemical analysis report", "Slope = sensitivity",
                   f"{r.fit.slope:.4g}", "505.1 mM", "Appendix", "Page 1 of", f"Page {pages} of {pages}"):
        assert needle in text, needle
    assert "opposite direction" in text                                                    # the A6 warning is in the PDF
    img = doc.render(1, doc.pagePointSize(1).toSize() * 2)
    assert not img.isNull() and img.width() > 600


def test_pdf_images_fit_inside_the_page(data, qapp):
    """Regression: <img width> is in 96-dpi logical pixels, not the 150-dpi device pixels the page uses, so
    figures were once 1.56x too wide and cropped at the right edge."""
    import re

    from PySide6.QtGui import QTextDocument

    from echem_analyzer import report_pdf
    px = report_pdf.RES / 25.4
    content_w = int(210 * px - (report_pdf.MARGIN_MM[0] + report_pdf.MARGIN_MM[2]) * px)     # A4 text width, device px
    html = report_pdf._html(build_report(data), QTextDocument(), content_w)
    widths = [int(w) for w in re.findall(r'<img src="fig\d+" width="(\d+)"', html)]
    limit = content_w * 96 / report_pdf.RES                                                    # same width in logical px
    assert len(widths) >= 10 and max(widths) <= limit + 1, (max(widths), limit)
    assert max(widths) > 0.9 * limit                                                           # and full-width figures do fill it


def test_pdf_of_an_empty_project_still_writes(qapp, tmp_path):
    out = tmp_path / "empty.pdf"
    assert write_pdf(build_report(ProjectData()), out) == 1 and out.stat().st_size > 1000


# ===================================================================== Excel workbook

def test_workbook_contents(data, tmp_path):
    out = tmp_path / "wb.xlsx"
    sheets = export_workbook(data, build_report(data), out)
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == sheets and sheets[0] == "Summary"
    assert all(len(n) <= 31 for n in sheets) and len({n.lower() for n in sheets}) == len(sheets)
    assert {"CA B2 GY (csv)", "CA B2 GY (csv) data", "CV B2 GY (csv)", "Compare"} <= set(sheets)

    ca_runs, _ = analysed_runs(data)
    c, s, r = next(x for x in ca_runs if "B2 GY" in x[0].source)
    ws = wb["Summary"]
    rows = [[cell for cell in row] for row in ws.iter_rows(values_only=True)]
    b2 = next(row for row in rows if row[0] and "B2 GY (csv) · Chronoamperometry" in str(row[0]))
    assert b2[3] == pytest.approx(r.sensitivity) and b2[4] == pytest.approx(r.fit.r2) and b2[7] == pytest.approx(r.c_x)
    # clickable contents list
    links = [cell.hyperlink.location for row in ws.iter_rows() for cell in row if cell.hyperlink]
    assert any("CA B2 GY (csv)" in (loc or "") for loc in links)

    # raw data is bit-for-bit what was loaded
    d = wb["CA B2 GY (csv) data"]
    vals = np.array([[v for v in row[:3]] for row in d.iter_rows(min_row=2, values_only=True)], float)
    assert np.allclose(vals[:, 0], c.x, atol=1e-6) and np.allclose(vals[:, 1], c.y, atol=1e-6)
    assert vals[:, 2].sum() == len(c.x) - s.drop_last                                      # the 'used' flag
    # the dashboard carries the setup, the results and the plots
    dash = wb["CA B2 GY (csv)"]
    flat = {str(v) for row in dash.iter_rows(values_only=True) for v in row if v not in (None, "")}
    assert {"Plateau mean", "505.1 mM", "10 mL"} <= flat and any(v.startswith("0.665") for v in flat)
    assert len(dash._images) == 2

    # one editable native chart per data sheet, pointing at that sheet's own columns
    with zipfile.ZipFile(out) as z:
        charts = [n for n in z.namelist() if n.startswith("xl/charts/chart")]
        assert len(charts) == 6                                                            # 3 CA + 3 CV
        body = " ".join(z.read(n).decode() for n in charts)
        assert "'CA B2 GY (csv) data'!$A$2:$A$1350" in body and "'CV B2 GY (csv) data'!" in body


def test_workbook_survives_missing_fits_and_awkward_names(data, tmp_path):
    for s in data.ca_settings.values():
        s.stock_conc = 0.0                                    # no calibration possible: NaN everywhere
    out = tmp_path / "nofit.xlsx"
    sheets = export_workbook(data, build_report(data), out)
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == sheets
    ws = wb["Summary"]
    errors = [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("#")]
    assert errors == []
    used: set[str] = set()
    names = [_sheet_name(n, used) for n in ("A" * 60, "A" * 60, "x[1]:y*z?/w\\", "Summary")]
    assert all(len(n) <= 31 for n in names) and len(set(n.lower() for n in names)) == 4
    assert not any(ch in n for n in names for ch in "[]:*?/\\")


# ===================================================================== project snapshot

def test_project_embeds_the_same_plots_as_the_report(data, tmp_path):
    p = tmp_path / "p.echemproj"
    save_project(p, data)
    with zipfile.ZipFile(p) as z:
        pngs = sorted(n for n in z.namelist() if n.startswith("plots/"))
        assert len(pngs) == len(build_report(data).figures()) == 14
        assert all(png_ok(z.read(n)) for n in pngs)
        assert any(n.startswith("plots/ca/") for n in pngs) and any(n.startswith("plots/cv/") for n in pngs)
        report_pngs = {f"plots/{k}": v.png for k, v in build_report(data).figures().items()}
        assert all(z.read(n) == report_pngs[n] for n in pngs)                                # identical to the PDF's figures
    back = load_project(p)                                                                   # plots are never read back
    assert len(back.files) == 3


def test_plots_can_be_left_out_of_a_project(data, tmp_path):
    p = tmp_path / "p.echemproj"
    save_project(p, data, include_plots=False)
    with zipfile.ZipFile(p) as z:
        assert not [n for n in z.namelist() if n.startswith("plots/")]
        assert "results/ca_summary.csv" in z.namelist()
