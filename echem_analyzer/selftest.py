"""``python -m echem_analyzer --selftest [FILE]``: exercise the installed app end to end without a display.

Run by the packaging build against the *frozen* application, so a missing library, plugin or data file fails the build instead of
reaching users. It imports every module, analyses a synthetic DPV series, builds the sensor study, writes a PDF report, an Excel
workbook and a project file and reads the project back, then opens the main window and draws every tab. The outcome goes to FILE
(or the console) because a windowed Windows program has no console. The exit code is 0 only if every step passed.
"""
from __future__ import annotations

import importlib
import json
import os
import pkgutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

import numpy as np

STEPS: list[tuple[str, "callable"]] = []


def step(name: str):
    def register(fn):
        STEPS.append((name, fn))
        return fn
    return register


def _synthetic_project():
    """A DPV calibration (0.8 µA per µM) as a ready-to-analyse project."""
    from .analyzers import ANALYZERS, upsert_responses
    from .models import Curve, DataFile, ProjectData, ProjectMeta
    e = np.linspace(-0.2, 0.6, 321)
    curves, responses = [], []
    for k, conc in enumerate((1, 2, 5, 10, 20, 50)):
        y = 0.8 * conc * np.exp(-0.5 * ((e - 0.2) / 0.03) ** 2) + 2.0 * e + 0.5
        curves.append(Curve(kind="DPV", title="DPV i vs E", x=e, y=y, x_unit="V", y_unit="µA", meta={"run_title": f"DPV {conc} µM"},
                            source="selftest.csv", uid=f"selftest#{k}"))
    an = ANALYZERS["peak"]
    for c in curves:
        res = an.run(c, an.defaults())
        assert res.ok, "peak analysis failed"
        upsert_responses(responses, c, "peak", [s for s in res.responses if s.quantity == "Peak height"])
    return ProjectData(meta=ProjectMeta(name="Self-test"), files=[DataFile(path=Path("selftest.csv"), fmt="csv", curves=curves)],
                       responses=responses)


@step("import every module")
def _imports(ctx):
    import echem_analyzer
    names = [m.name for m in pkgutil.walk_packages(echem_analyzer.__path__, "echem_analyzer.")]
    if len(names) < 20:                                  # a frozen app may not list its modules: fall back to the known ones
        names = ["echem_analyzer." + n for n in ("ca_analysis", "cv_analysis", "calibration", "peaks", "eis", "electrode", "transients",
                                                  "studies", "study_analysis", "standard_addition", "analyzers", "plots", "report",
                                                  "report_pdf", "workbook", "project", "presets", "prefs", "help_content",
                                                  "gui.main_window", "gui.ca_tab", "gui.study_tab", "gui.settings_dialog")]
    for n in names:
        importlib.import_module(n)
    return f"{len(names)} modules"


@step("trusted certificates for HTTPS")
def _trust_store(ctx):
    from . import updater
    cafile = updater.ca_bundle()
    assert cafile and Path(cafile).is_file(), "no list of trusted certificates: the app could not verify GitHub (the update check would fail)"
    n = updater.ssl_context().cert_store_stats()["x509_ca"]
    assert n >= 50, f"only {n} trusted CA certificates"
    return f"{n} trusted CA certificates (list: {Path(cafile).name})"


@step("analyse, calibrate, report")
def _analysis(ctx):
    from .report import build_report
    from .study_report import compute_study
    data = _synthetic_project()
    ctx["data"] = data
    study = compute_study(data.responses, data.extras)
    cs = study.calibrations[0]
    assert abs(cs.sensitivity - 0.8) < 0.05, f"sensitivity {cs.sensitivity}"
    report = build_report(data)
    ctx["report"] = report
    assert len(report.figures()) >= 5
    return f"sensitivity {cs.sensitivity:.3f} µA/µM, {len(report.figures())} figures"


@step("write PDF, Excel and project file")
def _outputs(ctx):
    from .project import load_project, save_project
    from .report_pdf import write_pdf
    from .workbook import export_workbook
    tmp = Path(ctx["tmp"])
    pages = write_pdf(ctx["report"], tmp / "report.pdf")
    sheets = export_workbook(ctx["data"], ctx["report"], tmp / "workbook.xlsx")
    save_project(tmp / "p.echemproj", ctx["data"])
    back = load_project(tmp / "p.echemproj")
    assert len(back.responses) == len(ctx["data"].responses)
    assert (tmp / "report.pdf").stat().st_size > 10_000 and (tmp / "workbook.xlsx").stat().st_size > 5_000
    return f"PDF {pages} pages, workbook {len(sheets)} sheets, project round trip"


@step("open the window and draw every tab")
def _window(ctx):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from .gui.main_window import MainWindow
    from .gui.session import Session
    app = QApplication.instance()
    qs = QSettings(str(Path(ctx["tmp"]) / "settings.ini"), QSettings.IniFormat)
    win = MainWindow(Session(qs))
    win.resize(1400, 900)
    win.show()
    win.session.add_datafile(ctx["data"].files[0])
    for r in ctx["data"].responses:
        win.session.responses.append(r)
    win.session.responses_changed.emit()
    for i in range(win.tabs.count()):
        win.tabs.setCurrentIndex(i)
        app.processEvents()
        assert not win.grab().isNull(), f"tab {win.tabs.tabText(i)} did not draw"
    win.show_help()
    win.show_settings()
    app.processEvents()
    win.session.dirty = False                           # nothing to save: close without asking
    win.close()
    return f"{win.tabs.count()} tabs"


def run(out: str | None = None) -> int:
    """Run every step; write a JSON outcome to ``out`` (or print it); return the exit code."""
    from PySide6.QtWidgets import QApplication
    from . import __version__
    app = QApplication.instance() or QApplication(sys.argv[:1])          # noqa: F841
    results, ok = [], True
    ctx: dict = {}
    with tempfile.TemporaryDirectory(prefix="echem-selftest-") as tmp:
        ctx["tmp"] = tmp
        for name, fn in STEPS:
            t0 = time.time()
            try:
                detail = fn(ctx)
                results.append({"step": name, "ok": True, "detail": detail, "seconds": round(time.time() - t0, 2)})
            except Exception:                                              # noqa: BLE001 - report every failure, then stop
                ok = False
                results.append({"step": name, "ok": False, "error": traceback.format_exc(), "seconds": round(time.time() - t0, 2)})
                break
    doc = {"version": __version__, "python": sys.version.split()[0], "platform": sys.platform, "frozen": bool(getattr(sys, "frozen", False)),
           "qpa": os.environ.get("QT_QPA_PLATFORM", ""), "ok": ok, "steps": results}
    text = json.dumps(doc, indent=2, ensure_ascii=False)
    if out:
        Path(out).write_text(text, encoding="utf-8")
    elif sys.stdout is not None:
        print(text)
    return 0 if ok else 1
