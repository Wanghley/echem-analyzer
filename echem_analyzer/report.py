"""The content of a report, independent of its format (no Qt imports).

``build_report`` turns a project (data + settings) into sections of tables and figures.  The PDF
writer, the Excel workbook and the project snapshot all render from this one description, so they
can never disagree with each other or with the numbers on screen.
"""
from __future__ import annotations

import copy
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Union

from . import __version__, plots
from .ca_analysis import analyze_ca
from .cv_analysis import CVRun, analyze_run, group_cv_runs, summarize_stability
from .labels import curve_run_label, curve_short_label, file_short
from .models import KIND_CA, CAAnalysisSettings, CAResult, CVSettings, Curve, ProjectData
from .palette import LIGHT
from .standard_addition import DILUTION_LABELS, SIGMA_LABELS
from .reporting import cv_cycle_table, cv_summary_rows, g, safe_name, step_table, summary_rows


# --------------------------------------------------------------------------- model

@dataclass
class FigureBlock:
    path: str                       # archive-relative name, e.g. "ca/B2__trace.png"
    caption: str
    png: bytes
    width: int
    height: int
    fraction: float = 1.0           # share of the text width it should take


@dataclass
class TableBlock:
    header: list[str]
    rows: list[list[str]]
    caption: str = ""
    align_right_from: int = 1       # columns from this index on are right-aligned (numbers)


@dataclass
class KeyValueBlock:
    rows: list[tuple[str, str]]
    caption: str = ""


@dataclass
class NoteBlock:
    text: str
    tone: str = "neutral"           # neutral | warning


Block = Union[FigureBlock, TableBlock, KeyValueBlock, NoteBlock]


@dataclass
class Section:
    title: str
    subtitle: str = ""
    blocks: list[Block] = field(default_factory=list)
    kind: str = "info"              # info | ca | cv | compare | study | electrode | analysis
    page_break: bool = False
    data: Any = None                # what a writer may want beyond the blocks (e.g. the calibration for a native chart)


@dataclass
class Report:
    title: str
    meta_rows: list[tuple[str, str]]
    generated: str
    version: str
    sections: list[Section]

    def figures(self) -> "OrderedDict[str, FigureBlock]":
        out: "OrderedDict[str, FigureBlock]" = OrderedDict()
        for s in self.sections:
            for b in s.blocks:
                if isinstance(b, FigureBlock):
                    out[b.path] = b
        return out


# --------------------------------------------------------------------------- figure cache

_CACHE: "OrderedDict[tuple, bytes]" = OrderedDict()
_CACHE_MAX = 96


def _cached(key: tuple, make) -> bytes:
    """Rendering a figure costs a few hundred ms; unchanged runs are re-used across saves."""
    from .prefs import prefs
    key = key + (prefs.signature(),)                          # a new plot style, palette or resolution must redraw
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        return hit
    png = make()
    _CACHE[key] = png
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return png


def clear_figure_cache() -> None:
    _CACHE.clear()


def _data_key(c: Curve) -> tuple:
    return (c.uid, len(c.x), float(c.y[0]), float(c.y[-1]), float(c.y.sum()))


def _settings_key(s: Any) -> str:
    return json.dumps(s.to_dict(), sort_keys=True, default=str)


# --------------------------------------------------------------------------- helpers

def _yn(v: bool) -> str:
    return "yes" if v else "no"


def _window(w: Optional[list[float]]) -> str:
    return "whole sweep" if not w else f"{w[0]:.3f} to {w[1]:.3f} V"


def ca_setup_rows(s: CAAnalysisSettings, n_steps: int) -> list[tuple[str, str]]:
    custom = [round(v, 6) for v in s.step_volumes_ul if abs(v - s.v_add_ul) > 1e-12]
    vol = f"{g(s.v_add_ul)} µL" + (" (custom schedule: " + ", ".join(g(v) for v in s.step_volumes_ul) + ")" if custom else "")
    excluded = [str(i + 1) for i, ok in enumerate(s.step_included) if not ok]
    return [
        ("Response method", "Drift-corrected" if s.method == "drift" else "Plateau mean"),
        ("Settle time / window / end guard", f"{g(s.settle)} s / {g(s.window)} s / {g(s.end_guard)} s"),
        ("Trailing points dropped", str(s.drop_last)),
        ("Additions detected", str(n_steps)),
        ("Stock concentration", f"{g(s.stock_conc)} {s.stock_unit}"),
        ("Cell volume V₀", f"{g(s.v0_ml)} mL"),
        ("Volume per addition", vol + (f"; verified {g(s.verified_ul)} µL used" if s.verified_ul > 0 else "")),
        ("Dilution correction", DILUTION_LABELS.get(s.dilution_mode, s.dilution_mode)),
        ("Sample volume", f"{g(s.v_sample_ml)} mL" if s.v_sample_ml > 0 else "= V₀ (undiluted)"),
        ("Background |i| subtracted", f"{g(s.background)} µA"),
        ("Electrode area", f"{g(s.area_cm2)} cm²" if s.area_cm2 > 0 else "not given"),
        ("σ for LOD/LOQ", SIGMA_LABELS.get(s.sigma_source, s.sigma_source)
         + (f" ({g(s.baseline_window)} s window)" if s.sigma_source == "baseline" else "")
         + (f" (n = {len(s.blank_replicates)})" if s.sigma_source == "blank" else "")),
        ("LOD / LOQ factors", f"{g(s.lod_k)}σ/S and {g(s.loq_k)}σ/S"),
        ("Suitability threshold", g(s.suitability_min)),
        ("Baseline point in the fit", _yn(s.include_baseline)),
        ("Points excluded from the fit", ", ".join(excluded) if excluded else "none"),
    ]


def cv_setup_rows(run: CVRun, s: CVSettings) -> list[tuple[str, str]]:
    return [
        ("Cycles", f"{len(run.cycles)} (numbers {', '.join(map(str, run.cycle_numbers))})"),
        ("Cycles excluded", ", ".join(map(str, s.excluded_cycles)) if s.excluded_cycles else "none"),
        ("Scan rate", f"{g(s.scan_rate)} V/s" if s.scan_rate > 0 else "unknown (no charge / capacitance)"),
        ("Anodic peak search", _window(s.anodic_window)),
        ("Cathodic peak search", _window(s.cathodic_window)),
        ("Baseline", "linear across a restricted window" if s.baseline == "linear" else "none (raw current)"),
        ("Stable if change below", f"{g(s.stability_tol)} %"),
    ]


def _fmt_rows(rows: list[list], digits: int = 4) -> list[list[str]]:
    return [[r if isinstance(r, str) else (str(r) if isinstance(r, (int, bool)) else g(r, digits)) for r in row]
            for row in rows]


# --------------------------------------------------------------------------- shared analysis

def analysed_runs(data: ProjectData) -> tuple[list[tuple[Curve, CAAnalysisSettings, CAResult]],
                                              list[tuple[CVRun, CVSettings]]]:
    """The runs that have settings (i.e. the ones the user opened), analysed on copies of the settings."""
    ca_runs: list[tuple[Curve, CAAnalysisSettings, CAResult]] = []
    for f in data.files:
        for c in f.curves:
            s = data.ca_settings.get(c.uid)
            if c.kind == KIND_CA and s is not None:
                s2 = copy.deepcopy(s)
                ca_runs.append((c, s2, analyze_ca(c.x, c.y, s2)))
    cv_runs: list[tuple[CVRun, CVSettings]] = []
    for run in group_cv_runs(c for f in data.files for c in f.curves):
        s = data.cv_settings.get(run.uid)
        if s is not None:
            cv_runs.append((run, copy.deepcopy(s)))
    return ca_runs, cv_runs


def ca_base(c: Curve) -> str:
    return safe_name(f"{Path(c.source).stem}__{c.meta.get('run_title') or c.title}")


def cv_base(run: CVRun) -> str:
    return safe_name(f"{Path(run.source).stem}__{run.title}")


# --------------------------------------------------------------------------- build

def build_report(data: ProjectData, with_figures: bool = True) -> Report:
    m = data.meta
    from .prefs import prefs
    meta_rows = [(k, v) for k, v in (("Project", m.name), ("Operator", m.operator or prefs.get("operator")),
                                     ("Institution", prefs.get("institution")), ("Electrode / sensor", m.electrode),
                                     ("Analyte", m.analyte), ("Sample matrix", m.matrix)) if v]
    sections: list[Section] = []

    # ---- overview: what is in the project
    inv = []
    for f in data.files:
        n_ca, n_cv = len(f.of_kind(KIND_CA)), sum(1 for c in f.curves if c.kind == "CV")
        inv.append([f.name, f.fmt, str(n_ca), str(n_cv), str(len(f.curves) - n_ca - n_cv)])
    overview = Section("Overview", kind="info")
    if m.notes:
        overview.blocks.append(NoteBlock(m.notes))
    overview.blocks.append(TableBlock(["Data file", "Format", "CA runs", "CV scans", "Other measurements"], inv,
                                      "Data files in this project", align_right_from=2))
    sections.append(overview)

    ca_runs, cv_runs = analysed_runs(data)
    from . import study_report                      # imported here: study_report builds on this module's blocks
    study = study_report.compute_study(list(data.responses), data.extras) if data.responses else study_report.StudyResults()

    # ---- summary table across runs
    if ca_runs or cv_runs or study.calibrations:
        summ = Section("Summary of results", kind="info")
        if ca_runs:
            rows = []
            for c, s, r in ca_runs:
                f = r.fit
                rows.append([file_short(c.source), curve_short_label(c), "drift" if s.method == "drift" else "plateau",
                             str(len(r.step_times)), g(r.sensitivity) if f else "—", g(f.r2, 4) if f else "—",
                             g(r.lod, 3) if f else "—", g(r.loq, 3) if f else "—",
                             g(r.c_x, 4) if f else "—", s.stock_unit])
            summ.blocks.append(TableBlock(
                ["File", "Run", "Method", "Steps", "Sensitivity (µA/unit)", "R²", "LOD", "LOQ", "C_x", "Unit"],
                rows, "Chronoamperometry: standard addition", align_right_from=3))
        if cv_runs:
            rows = []
            for run, s in cv_runs:
                res = analyze_run(run, s)
                st = summarize_stability(res, s.stability_tol) if res else None
                last = res[-1] if res else None
                rows.append([file_short(run.source), run.title, f"{len(res)}/{len(run.cycles)}",
                             g(last.i_pa) if last else "—", g(last.i_pc) if last else "—",
                             g(st["i_pa"].last_change_pct, 3) if st and len(res) > 1 else "—",
                             (f"cycle {st['i_pa'].stable_from}" if st and st["i_pa"].stable_from is not None else
                              ("not yet" if st and len(res) > 1 else "—"))])
            summ.blocks.append(TableBlock(
                ["File", "Run", "Cycles used", "Anodic ip (µA)", "Cathodic ip (µA)", "Last change (%)", "Anodic stable from"],
                rows, "Cyclic voltammetry: multi-cycle runs", align_right_from=2))
        if study.calibrations:
            summ.blocks.append(study_report.calibration_summary_table(study.calibrations))
        sections.append(summ)

    # ---- one section per CA run
    for c, s, r in ca_runs:
        sec = Section(curve_run_label(c), subtitle=f"Chronoamperometry · {c.n} points", kind="ca", page_break=True)
        base = ca_base(c)
        sec.blocks.append(KeyValueBlock(ca_setup_rows(s, len(r.step_times)), "Setup"))
        sec.blocks.append(KeyValueBlock([(n, f"{v} {u}".strip()) for n, v, u in summary_rows(s, r)
                                         if n not in ("Response method", "Additions")], "Results"))
        for w in r.warnings:
            sec.blocks.append(NoteBlock(w, "warning"))
        if with_figures:
            sk = _settings_key(s)
            title = f"{file_short(c.source)} — {curve_short_label(c)}"
            png = _cached(("ca-trace", _data_key(c), sk), lambda: plots.render_png(
                lambda fig, p: plots.ca_trace(fig.add_subplot(111), c, s, r, p, title=title), (8.0, 2.9)))
            sec.blocks.append(FigureBlock(f"ca/{base}__trace.png", "Current vs time with detected additions", png,
                                          *plots.png_size(png), 1.0))
            png = _cached(("ca-cal", _data_key(c), sk), lambda: plots.render_png(
                lambda fig, p: plots.ca_calibration(fig, s, r, p), (6.0, 3.9)))
            sec.blocks.append(FigureBlock(f"ca/{base}__calibration.png", "Standard-addition calibration", png,
                                          *plots.png_size(png), 0.5))
        sections.append(sec)

    # ---- one section per CV run
    for run, s in cv_runs:
        res = analyze_run(run, s)
        sec = Section(f"{file_short(run.source)} · {run.title}", subtitle=f"Cyclic voltammetry · {len(run.cycles)} cycles",
                      kind="cv", page_break=True)
        base = cv_base(run)
        sec.blocks.append(KeyValueBlock(cv_setup_rows(run, s), "Setup"))
        sec.blocks.append(KeyValueBlock([(n, f"{v} {u}".strip()) for n, v, u in cv_summary_rows(run, s)
                                         if n != "Cycles analysed"], "Results"))
        if with_figures:
            key = (run.uid, tuple(_data_key(c) for c in run.cycles.values()), _settings_key(s))
            png = _cached(("cv-cycles",) + key, lambda: plots.render_png(
                lambda fig, p: plots.cv_cycles(fig.add_subplot(111), run, s, res, p, branches=True,
                                               title=f"{file_short(run.source)} — {run.title}"), (6.2, 4.6)))
            sec.blocks.append(FigureBlock(f"cv/{base}__cycles.png", "Voltammograms, light to dark by cycle", png,
                                          *plots.png_size(png), 0.58))
            png = _cached(("cv-trends",) + key, lambda: plots.render_png(
                lambda fig, p: plots.cv_trends(fig, res, s, p), (5.2, 4.6)))
            sec.blocks.append(FigureBlock(f"cv/{base}__trends.png", "Peak current and charge per cycle", png,
                                          *plots.png_size(png), 0.42))
        header, rows = cv_cycle_table(run, s)
        sec.blocks.append(TableBlock(
            ["Cycle", "Used", "E pa (V)", "i pa (µA)", "E pc (V)", "i pc (µA)", "E½ (V)", "ΔEp (V)", "ipa/ipc",
             "Qa (µC)", "Qc (µC)", "C app (µF)", "Δ ipa (%)"],
            [[str(r[0]), "yes" if r[1] else "no"] + _fmt_rows([r[2:]], 4)[0] for r in rows], "Cycles", align_right_from=2))
        sections.append(sec)

    # ---- comparison
    if with_figures and len(ca_runs) >= 2:
        sec = Section("Comparison of runs", subtitle=f"{len(ca_runs)} chronoamperometry runs", kind="compare", page_break=True)
        keyc = tuple((_data_key(c), _settings_key(s)) for c, s, _ in ca_runs)
        png = _cached(("cmp-traces", keyc), lambda: plots.render_png(
            lambda fig, p: plots.compare_traces(fig.add_subplot(111), ca_runs, p, True, True, curve_run_label), (6.6, 4.4)))
        sec.blocks.append(FigureBlock("compare/ca_traces.png", "Traces aligned at the first addition, baseline subtracted",
                                      png, *plots.png_size(png), 0.54))
        png = _cached(("cmp-cal", keyc), lambda: plots.render_png(
            lambda fig, p: plots.compare_calibrations(fig.add_subplot(111), ca_runs, p, True, curve_run_label), (6.0, 4.4)))
        sec.blocks.append(FigureBlock("compare/calibrations.png", "Calibration lines", png, *plots.png_size(png), 0.46))
        sections.append(sec)

    # ---- sensor study, electrode characterisation and the analyses behind the responses
    sections += study_report.study_sections(data, with_figures, study)
    sections += study_report.electrode_sections(data, with_figures)
    sections += study_report.analysis_sections(data, with_figures)

    # ---- appendix: the full step tables (kept out of the run pages so each run fits on one page)
    if ca_runs:
        app = Section("Appendix · step tables", subtitle="Plateau currents and signal of every segment", kind="info",
                      page_break=True)
        for c, s, r in ca_runs:
            header, rows = step_table(s, r)
            app.blocks.append(TableBlock(
                ["Segment", "Used", "t (s)", "V (µL)", f"C ({s.stock_unit})", "Override", "I (µA)", "± sd", "Δi (µA)", "Signal (µA)"],
                [[str(row[0]), "yes" if row[1] else "no"] + _fmt_rows([row[2:]])[0] for row in rows],
                curve_run_label(c), align_right_from=2))
        sections.append(app)

    # ---- methods
    methods = Section("About this report", kind="info")
    methods.blocks.append(NoteBlock(
        "Standard addition: the plateau |i| of each segment is fitted against the added concentration "
        "C = C_s·ΣV / (V₀+ΣV); the x-intercept gives C_x = intercept / slope. LOD = 3σ/S and LOQ = 10σ/S. "
        "Cyclic voltammetry: the anodic peak is the maximum on the positive-going sweep and the cathodic peak the "
        "minimum on the negative-going sweep; charge is ∫ i dt per sweep and the apparent capacitance "
        "(Q_a − Q_c)/(2ΔE) also contains any Faradaic current. "
        "Sensor study: calibrations are least-squares fits (linear, log-linear, Michaelis–Menten or Hill, chosen by AIC); "
        "sensitivity is the slope, LOD = kσ/S and LOQ = 10σ/S with σ from blank replicates or the fit residuals, and "
        "unknowns are read from the fitted curve by inverse prediction. Reproducibility is the RSD between electrodes "
        "at the same concentration, stability the retention of a response over time, selectivity the response to "
        "interferents relative to the analyte."))
    sections.append(methods)

    return Report(title=m.name or "Echem Analyzer report", meta_rows=meta_rows,
                  generated=datetime.now().strftime("%Y-%m-%d %H:%M"), version=__version__, sections=sections)
