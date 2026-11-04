"""Excel workbook export (no Qt imports): raw data, setup, results and plots in one file.

Layout: a Summary sheet with links; for every analysed run a *dashboard* sheet (setup, results, steps or
cycles, and the plots as images) and a *data* sheet (the raw numbers plus an editable native Excel chart).
"""
from __future__ import annotations

import io
import math
import re
from pathlib import Path
from typing import Any, Optional

import numpy as np
import xlsxwriter

from . import __version__
from . import calibration as cal
from .cv_analysis import CVRun, analyze_run, summarize_stability
from .labels import curve_run_label, curve_short_label, file_short
from .models import CAAnalysisSettings, CAResult, CVSettings, Curve, ProjectData
from .palette import LIGHT
from .plots import png_dpi
from .report import (
    FigureBlock, KeyValueBlock, NoteBlock, Report, TableBlock, analysed_runs, ca_base, ca_setup_rows, cv_base,
    cv_setup_rows,
)
from .study_report import responses_table
from .reporting import cv_cycle_table, cv_summary_rows, g, step_table, summary_rows

P = LIGHT
_BAD = re.compile(r"[\[\]:*?/\\]")


def _sheet_name(base: str, used: set[str]) -> str:
    name = _BAD.sub(" ", base).strip()[:31] or "Sheet"
    cand, k = name, 2
    while cand.lower() in used:
        suffix = f" {k}"
        cand, k = name[: 31 - len(suffix)] + suffix, k + 1
    used.add(cand.lower())
    return cand


def _num(x: Any) -> Any:
    """Excel cell value: blank for NaN / inf (xlsxwriter would refuse them)."""
    if isinstance(x, (str, bool)) or x is None:
        return x
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    return "" if not math.isfinite(v) else v


class _Formats:
    def __init__(self, wb: xlsxwriter.Workbook):
        base = {"font_name": "Helvetica Neue", "font_size": 10, "font_color": P.text, "valign": "vcenter"}
        mk = lambda **kw: wb.add_format({**base, **kw})          # noqa: E731
        self.title = mk(font_size=18, bold=True)
        self.subtitle = mk(font_color=P.muted, font_size=10)
        self.band = mk(font_size=20, bold=True, font_color="#FFFFFF", bg_color=P.accent, indent=1)
        self.band_sub = mk(font_color="#DCE8FF", bg_color=P.accent, indent=1)
        self.section = mk(bold=True, font_color=P.muted, font_size=9, bottom=1, bottom_color=P.border)
        self.key = mk(font_color=P.muted, bottom=1, bottom_color=P.border)
        self.val = mk(bold=True, bottom=1, bottom_color=P.border, align="left")
        self.th = mk(bold=True, font_color=P.muted, bg_color=P.surface2, font_size=9, bottom=1, bottom_color=P.border,
                     align="center", text_wrap=True)
        self.th_left = mk(bold=True, font_color=P.muted, bg_color=P.surface2, font_size=9, bottom=1,
                          bottom_color=P.border, align="left", text_wrap=True)
        self.td = mk(bottom=1, bottom_color=P.border, num_format="0.0000")
        self.td_text = mk(bottom=1, bottom_color=P.border, align="left")
        self.td_int = mk(bottom=1, bottom_color=P.border, num_format="0", align="center")
        self.td_num3 = mk(bottom=1, bottom_color=P.border, num_format="0.000")
        self.td_gen = mk(bottom=1, bottom_color=P.border, num_format="General", align="right")
        self.warn = mk(font_color=P.warning, bg_color=P.warning_soft, text_wrap=True, valign="top", indent=1)
        self.note = mk(font_color=P.muted, text_wrap=True, valign="top")
        self.link = mk(font_color=P.accent, underline=1)
        self.raw = mk(num_format="0.000000")
        self.raw_head = mk(bold=True, bg_color=P.surface2, font_color=P.muted, font_size=9, bottom=1, bottom_color=P.border)


def _kv(ws, f: _Formats, row: int, col: int, rows: list[tuple[str, str]], caption: str) -> int:
    ws.write(row, col, caption.upper(), f.section)
    ws.write(row, col + 1, "", f.section)
    for i, (k, v) in enumerate(rows, start=1):
        ws.write(row + i, col, k, f.key)
        ws.write(row + i, col + 1, v, f.val)
    return row + len(rows) + 1


def _table(ws, f: _Formats, row: int, col: int, header: list[str], rows: list[list], caption: str = "",
           text_cols: tuple[int, ...] = (0,), general: bool = False) -> int:
    if caption:
        ws.write(row, col, caption.upper(), f.section)
        for j in range(1, len(header)):
            ws.write(row, col + j, "", f.section)
        row += 1
    for j, h in enumerate(header):
        ws.write(row, col + j, h, f.th_left if j in text_cols else f.th)
    for i, r in enumerate(rows, start=1):
        for j, v in enumerate(r):
            fmt = f.td_text if j in text_cols else (f.td_gen if general else f.td)
            ws.write(row + i, col + j, _num(v) if not isinstance(v, str) else v, fmt)
    return row + len(rows) + 1


def _insert_png(ws, row: int, col: int, png: bytes, name: str, width_px: int = 640) -> int:
    """Insert a PNG scaled to ``width_px`` and return the number of rows it covers (20 px rows)."""
    w = int.from_bytes(png[16:20], "big")
    h = int.from_bytes(png[20:24], "big")
    # The PNGs carry their resolution (set by the export dpi preference) and xlsxwriter already converts that to
    # 96-dpi pixels, so the scale only has to bring that natural size to the wanted width.
    dpi = png_dpi(png)
    natural_px = w * 96 / dpi
    scale = width_px / natural_px
    ws.insert_image(row, col, name, {"image_data": io.BytesIO(png), "x_scale": scale, "y_scale": scale,
                                     "object_position": 3})
    return int(h * 96 / dpi * scale / 20) + 2


def _dashboard_header(ws, f: _Formats, title: str, subtitle: str, width_cols: int = 12) -> None:
    ws.hide_gridlines(2)
    ws.set_row(0, 34)
    ws.merge_range(0, 0, 0, width_cols, title, f.band)
    ws.merge_range(1, 0, 1, width_cols, subtitle, f.band_sub)


# --------------------------------------------------------------------------- sheets

def _ca_sheets(wb, f, names: set[str], c: Curve, s: CAAnalysisSettings, r: CAResult, report: Report,
               figures: dict, index: list[tuple[str, str, str]]) -> None:
    dash = _sheet_name(f"CA {file_short(c.source)}", names)
    raw = _sheet_name(f"{dash} data", names)
    ws = wb.add_worksheet(dash)
    ws.set_column(0, 0, 32)
    ws.set_column(1, 1, 30)
    ws.set_column(2, 2, 3)
    ws.set_column(3, 3, 30)
    ws.set_column(4, 4, 30)
    ws.set_column(5, 5, 3)
    ws.set_column(6, 14, 11)
    _dashboard_header(ws, f, f"{file_short(c.source)} · {curve_short_label(c)}",
                      f"Chronoamperometry · {c.n} points · standard addition", 14)
    row = 3
    if r.warnings:
        for w in r.warnings:
            ws.merge_range(row, 0, row, 4, "⚠  " + w, f.warn)
            ws.set_row(row, 32)
            row += 1
        row += 1
    end_a = _kv(ws, f, row, 0, ca_setup_rows(s, len(r.step_times)), "Setup")
    end_b = _kv(ws, f, row, 3, [(n, f"{v} {u}".strip()) for n, v, u in summary_rows(s, r)
                                if n not in ("Response method", "Additions")], "Results")
    row = max(end_a, end_b) + 2

    header, rows = step_table(s, r)
    row = _table(ws, f, row, 0, ["Segment", "Used", "t (s)", "V (µL)", f"C ({s.stock_unit})", "Override", "I (µA)",
                                 "± sd (µA)", "Δi (µA)", "Signal (µA)"],
                 [[str(x[0]), "yes" if x[1] else "no"] + list(x[2:]) for x in rows], "Steps", text_cols=(0, 1))
    # plots on the right
    base = ca_base(c)
    pr = 3
    for suffix, label in (("trace", "Current vs time"), ("calibration", "Calibration")):
        fig = figures.get(f"ca/{base}__{suffix}.png")
        if fig is not None:
            pr += _insert_png(ws, pr, 6, fig.png, f"{base}_{suffix}.png", 700 if suffix == "trace" else 520)

    # raw data + editable native chart
    wd = wb.add_worksheet(raw)
    wd.set_column(0, 1, 14)
    wd.freeze_panes(1, 0)
    wd.write(0, 0, "t (s)", f.raw_head)
    wd.write(0, 1, f"i ({c.y_unit})", f.raw_head)
    wd.write(0, 2, "used", f.raw_head)
    n_used = len(c.x) - s.drop_last if s.drop_last > 0 else len(c.x)
    for i, (xx, yy) in enumerate(zip(c.x, c.y), start=1):
        wd.write_number(i, 0, float(xx), f.raw)
        wd.write_number(i, 1, float(yy), f.raw)
        wd.write_number(i, 2, 1 if i <= n_used else 0)
    ch = wb.add_chart({"type": "scatter", "subtype": "straight"})
    ch.add_series({"name": curve_short_label(c), "categories": [raw, 1, 0, len(c.x), 0],
                   "values": [raw, 1, 1, len(c.x), 1], "line": {"color": P.trace, "width": 1.25}})
    ch.set_title({"name": "Chronoamperogram", "name_font": {"size": 11}})
    ch.set_x_axis({"name": "Time (s)", "major_gridlines": {"visible": True, "line": {"color": P.grid}}})
    ch.set_y_axis({"name": f"Current ({c.y_unit})", "major_gridlines": {"visible": True, "line": {"color": P.grid}}})
    ch.set_legend({"none": True})
    ch.set_size({"width": 640, "height": 340})
    wd.insert_chart(1, 4, ch)
    index.append((dash, "Chronoamperometry", f"{file_short(c.source)} · {curve_short_label(c)}"))
    index.append((raw, "  raw data + chart", ""))


def _cv_sheets(wb, f, names: set[str], run: CVRun, s: CVSettings, report: Report, figures: dict,
               index: list[tuple[str, str, str]]) -> None:
    dash = _sheet_name(f"CV {file_short(run.source)}", names)
    raw = _sheet_name(f"{dash} data", names)
    ws = wb.add_worksheet(dash)
    ws.set_column(0, 0, 32)
    ws.set_column(1, 1, 30)
    ws.set_column(2, 2, 3)
    ws.set_column(3, 3, 30)
    ws.set_column(4, 4, 30)
    ws.set_column(5, 5, 3)
    ws.set_column(6, 16, 11)
    _dashboard_header(ws, f, f"{file_short(run.source)} · {run.title}",
                      f"Cyclic voltammetry · {len(run.cycles)} cycles", 16)
    res = analyze_run(run, s)
    end_a = _kv(ws, f, 3, 0, cv_setup_rows(run, s), "Setup")
    end_b = _kv(ws, f, 3, 3, [(n, f"{v} {u}".strip()) for n, v, u in cv_summary_rows(run, s) if n != "Cycles analysed"],
                "Results")
    row = max(end_a, end_b) + 2
    header, rows = cv_cycle_table(run, s)
    nice = ["Cycle", "Used", "E pa (V)", "i pa (µA)", "E pc (V)", "i pc (µA)", "E½ (V)", "ΔEp (V)", "ipa/ipc",
            "Qa (µC)", "Qc (µC)", "C app (µF)", "Δ ipa (%)"]
    _table(ws, f, row, 0, nice, [[str(r[0]), "yes" if r[1] else "no"] + list(r[2:]) for r in rows], "Cycles",
           text_cols=(0, 1))
    base = cv_base(run)
    pr = 3
    for suffix, w in (("cycles", 560), ("trends", 520)):
        fig = figures.get(f"cv/{base}__{suffix}.png")
        if fig is not None:
            pr += _insert_png(ws, pr, 6, fig.png, f"{base}_{suffix}.png", w)

    wd = wb.add_worksheet(raw)
    wd.freeze_panes(1, 0)
    ch = wb.add_chart({"type": "scatter", "subtype": "straight"})
    for k, n in enumerate(run.cycle_numbers):
        cv = run.cycles[n]
        c0 = 2 * k
        wd.set_column(c0, c0 + 1, 13)
        wd.write(0, c0, f"E (V) · cycle {n}", f.raw_head)
        wd.write(0, c0 + 1, f"i (µA) · cycle {n}", f.raw_head)
        for i, (xx, yy) in enumerate(zip(cv.x, cv.y), start=1):
            wd.write_number(i, c0, float(xx), f.raw)
            wd.write_number(i, c0 + 1, float(yy), f.raw)
        ch.add_series({"name": f"cycle {n}", "categories": [raw, 1, c0, len(cv.x), c0],
                       "values": [raw, 1, c0 + 1, len(cv.x), c0 + 1],
                       "line": {"color": P.series[k % len(P.series)], "width": 1.25}})
    ch.set_title({"name": "Voltammograms", "name_font": {"size": 11}})
    ch.set_x_axis({"name": "Potential (V)", "major_gridlines": {"visible": True, "line": {"color": P.grid}}})
    ch.set_y_axis({"name": "Current (µA)", "major_gridlines": {"visible": True, "line": {"color": P.grid}}})
    ch.set_size({"width": 640, "height": 400})
    wd.insert_chart(1, 2 * len(run.cycle_numbers) + 1, ch)
    index.append((dash, "Cyclic voltammetry", f"{file_short(run.source)} · {run.title}"))
    index.append((raw, "  raw data + chart", ""))


# --------------------------------------------------------------------------- entry point


# --------------------------------------------------------------------------- sensor study, electrode and analyses

def _cell(value: Any) -> Any:
    """Numbers that a report table holds as text become numbers in the workbook."""
    if isinstance(value, str):
        try:
            v = float(value.strip())
        except ValueError:
            return value
        return v if math.isfinite(v) else value
    return value


def _set_generic_columns(ws) -> None:
    ws.set_column(0, 0, 30)
    ws.set_column(1, 5, 24)
    ws.set_column(6, 14, 14)


def _note_row(ws, f, row: int, block: NoteBlock) -> int:
    ws.merge_range(row, 0, row, 5, ("⚠  " if block.tone == "warning" else "") + block.text,
                   f.warn if block.tone == "warning" else f.note)
    ws.set_row(row, 15 * (1 + len(block.text) // 110) + 4)
    return row + 2


def _blocks_to_sheet(ws, f, section, row: int, label: str) -> int:
    """Render the key/value tables, tables, notes and figures of a report section down a sheet."""
    blocks, i = section.blocks, 0
    while i < len(blocks):
        b = blocks[i]
        if isinstance(b, KeyValueBlock):
            group = [b]
            if i + 1 < len(blocks) and isinstance(blocks[i + 1], KeyValueBlock):
                group.append(blocks[i + 1])
            row = max(_kv(ws, f, row, 2 * k, x.rows, x.caption) for k, x in enumerate(group)) + 1
            i += len(group)
            continue
        if isinstance(b, TableBlock):
            row = _table(ws, f, row, 0, b.header, [[_cell(c) for c in r] for r in b.rows], b.caption, general=True) + 1
        elif isinstance(b, FigureBlock):
            row += _insert_png(ws, row, 0, b.png, f"{label}_{b.path.replace('/', '_')}", int(760 * min(1.0, max(b.fraction, 0.5))))
        else:
            row = _note_row(ws, f, row, b)
        i += 1
    return row


def _calibration_chart(wb, ws, f, sheet: str, cs, row: int) -> int:
    """The calibration data and an editable native scatter chart (points and fitted curve)."""
    n = len(cs.x)
    ws.write(row, 0, "CALIBRATION DATA", f.section)
    for j in range(1, 6):
        ws.write(row, j, "", f.section)
    heads = [f"Concentration ({cs.conc_unit})", f"{cs.options.quantity} ({cs.y_unit})", "Electrode", "", f"fit x ({cs.conc_unit})", "fit y"]
    for j, h in enumerate(heads):
        ws.write(row + 1, j, h, f.th)
    for i, (x, y, g_) in enumerate(zip(cs.x, cs.y, cs.groups), start=2):
        ws.write_number(row + i, 0, float(x), f.td)
        ws.write_number(row + i, 1, float(y), f.td)
        ws.write(row + i, 2, g_ or "", f.td_text)
    lo, hi = float(np.min(cs.x)), float(np.max(cs.x))
    xs = np.linspace(min(lo, 0.0) if cs.model == cal.LINEAR else lo, hi, 60)
    ys = cs.fit.predict(xs)
    m = 0
    for x, y in zip(xs, ys):
        if math.isfinite(x) and math.isfinite(y):
            ws.write_number(row + 2 + m, 4, float(x), f.td)
            ws.write_number(row + 2 + m, 5, float(y), f.td)
            m += 1
    ch = wb.add_chart({"type": "scatter"})
    ch.add_series({"name": "standards", "categories": [sheet, row + 2, 0, row + 1 + n, 0], "values": [sheet, row + 2, 1, row + 1 + n, 1],
                   "marker": {"type": "circle", "size": 7, "fill": {"color": P.trace}, "border": {"color": P.trace}}, "line": {"none": True}})
    if m:
        ch.add_series({"name": cal.MODEL_LABELS[cs.model], "categories": [sheet, row + 2, 4, row + 1 + m, 4],
                       "values": [sheet, row + 2, 5, row + 1 + m, 5], "marker": {"type": "none"}, "line": {"color": P.fit, "width": 1.75}})
    ch.set_title({"name": f"Calibration · {cs.options.quantity}", "name_font": {"size": 11}})
    ch.set_x_axis({"name": f"Concentration ({cs.conc_unit})", "major_gridlines": {"visible": True, "line": {"color": P.grid}}})
    ch.set_y_axis({"name": f"{cs.options.quantity} ({cs.y_unit})", "major_gridlines": {"visible": True, "line": {"color": P.grid}}})
    ch.set_legend({"position": "bottom"})
    ch.set_size({"width": 560, "height": 340})
    ws.insert_chart(row + 1, 7, ch)
    return row + max(n, m, 18) + 4


def _responses_sheet(wb, f, names: set[str], data: ProjectData, index: list) -> None:
    ws = wb.add_worksheet(_sheet_name("Responses", names))
    ws.hide_gridlines(2)
    header, rows = responses_table(list(data.responses))
    ws.set_column(0, 0, 44)
    ws.set_column(1, 3, 18)
    ws.set_column(4, 5, 10)
    ws.set_column(6, 10, 14)
    ws.set_column(11, 11, 40)
    heads = header + ["Note"]
    for j, h in enumerate(heads):
        ws.write(0, j, h, f.th_left if j < 5 else f.th)
    for i, r in enumerate(data.responses, start=1):
        vals = [r.source_label or r.id, r.quantity, r.condition, r.group, r.role, "yes" if r.use else "no"]
        for j, v in enumerate(vals):
            ws.write(i, j, v, f.td_text)
        ws.write(i, 6, _num(r.value), f.td)
        ws.write(i, 7, _num(r.sd) if r.sd is not None else "", f.td)
        ws.write(i, 8, r.unit, f.td_text)
        ws.write(i, 9, _num(r.conc) if r.conc is not None else "", f.td)
        ws.write(i, 10, _num(r.time) if r.time is not None else "", f.td)
        ws.write(i, 11, r.note, f.td_text)
    ws.write(0, 12, "Conc. unit", f.th)
    for i, r in enumerate(data.responses, start=1):
        ws.write(i, 12, r.conc_unit if r.conc is not None else "", f.td_text)
    ws.freeze_panes(1, 1)
    ws.autofilter(0, 0, max(len(data.responses), 1), 12)
    index.append((ws.get_name(), "Sensor study", f"{len(data.responses)} responses (all rows, filterable)"))


def _study_sheets(wb, f, names: set[str], report: Report, index: list) -> None:
    for sec in report.sections:
        if sec.kind not in ("study", "electrode"):
            continue
        ws = wb.add_worksheet(_sheet_name(sec.title, names))
        _set_generic_columns(ws)
        _dashboard_header(ws, f, sec.title, sec.subtitle or "Sensor study", 12)
        row = _blocks_to_sheet(ws, f, sec, 3, ws.get_name().replace(" ", "_"))
        if sec.data is not None and getattr(sec.data, "ok", False):
            _calibration_chart(wb, ws, f, ws.get_name(), sec.data, row)
        index.append((ws.get_name(), "Electrode" if sec.kind == "electrode" else "Sensor study", sec.subtitle or sec.title))


def _analysis_sheets(wb, f, names: set[str], data: ProjectData, report: Report, index: list) -> None:
    secs = [s for s in report.sections if s.kind == "analysis" and isinstance(s.data, dict)]
    if not secs:
        return
    curves = {c.uid: c for fl in data.files for c in fl.curves}
    by_analyzer: dict[str, list] = {}
    for s in secs:
        by_analyzer.setdefault(s.data["analyzer"], []).append(s)
    n_img = 0
    for akey, group in by_analyzer.items():
        title = group[0].subtitle.split(" · ")[0]
        ws = wb.add_worksheet(_sheet_name(title, names))
        _set_generic_columns(ws)
        _dashboard_header(ws, f, title, f"{len(group)} measurement" + ("s" if len(group) > 1 else ""), 12)
        row = 3
        for s in group:
            ws.merge_range(row, 0, row, 5, s.title, f.th_left)
            row += 1
            kvs = [b for b in s.blocks if isinstance(b, KeyValueBlock)]
            ends = [_kv(ws, f, row, 2 * k, b.rows, b.caption) for k, b in enumerate(kvs[:2])]
            fig = next((b for b in s.blocks if isinstance(b, FigureBlock)), None)
            n_img += 1
            fig_rows = _insert_png(ws, row, 6, fig.png, f"{akey}_{n_img}", 520) if fig else 0
            row = max(max(ends, default=row), row + fig_rows) + 1
            for b in s.blocks:
                if isinstance(b, NoteBlock):
                    row = _note_row(ws, f, row, b)
                elif isinstance(b, TableBlock):
                    row = _table(ws, f, row, 0, b.header, [[_cell(c) for c in r] for r in b.rows], b.caption, general=True) + 1
            row += 1
        index.append((ws.get_name(), "Analysis", f"{title}: {len(group)} measurement" + ("s" if len(group) > 1 else "")))

    # the raw numbers of every analysed measurement, side by side
    raw = wb.add_worksheet(_sheet_name("Raw data", names))
    raw.freeze_panes(2, 0)
    col, done = 0, set()
    for s in secs:
        uid = s.data["uid"]
        if uid in done:
            continue
        done.add(uid)
        c = curves[uid]
        cols = [(c.x_unit, c.x), (c.y_unit, c.y)] + [(k, v) for k, v in sorted(c.extra.items()) if len(v) == c.n]
        raw.merge_range(0, col, 0, col + len(cols) - 1, curve_run_label(c), f.raw_head)
        for j, (unit, arr) in enumerate(cols):
            raw.set_column(col + j, col + j, 14)
            raw.write(1, col + j, unit, f.raw_head)
            for i, v in enumerate(arr):
                if math.isfinite(float(v)):
                    raw.write_number(2 + i, col + j, float(v), f.raw)
        col += len(cols) + 1
    index.append((raw.get_name(), "Analysis", "raw numbers of every analysed measurement"))

def export_workbook(data: ProjectData, report: Report, path: str | Path) -> list[str]:
    """Write ``data`` and its analysis to an .xlsx file; returns the sheet names in order."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(str(path))
    wb.set_properties({"title": report.title, "subject": "Electrochemical analysis",
                       "author": data.meta.operator or "", "comments": f"Created with Echem Analyzer {__version__}"})
    f = _Formats(wb)
    figures = dict(report.figures())
    ca_runs, cv_runs = analysed_runs(data)

    summary = wb.add_worksheet("Summary")
    names: set[str] = {"summary"}
    index: list[tuple[str, str, str]] = []

    for c, s, r in ca_runs:
        _ca_sheets(wb, f, names, c, s, r, report, figures, index)
    for run, s in cv_runs:
        _cv_sheets(wb, f, names, run, s, report, figures, index)

    # ---- sensor study, electrode characterisation and the analyses behind the responses
    if data.responses:
        _responses_sheet(wb, f, names, data, index)
    _study_sheets(wb, f, names, report, index)
    _analysis_sheets(wb, f, names, data, report, index)

    # ---- comparison sheet
    if len(ca_runs) >= 2:
        wc = wb.add_worksheet(_sheet_name("Compare", names))
        wc.set_column(0, 0, 36)
        wc.set_column(1, 12, 14)
        _dashboard_header(wc, f, "Comparison of runs", f"{len(ca_runs)} chronoamperometry runs", 12)
        rows = []
        for c, s, r in ca_runs:
            fit = r.fit
            rows.append([curve_run_label(c), "drift" if s.method == "drift" else "plateau", len(r.step_times),
                         r.sensitivity if fit else "", fit.r2 if fit else "", r.lod if fit else "",
                         r.loq if fit else "", r.c_x if fit else "", s.stock_unit])
        end = _table(wc, f, 3, 0, ["Run", "Method", "Steps", "Sensitivity (µA/unit)", "R²", "LOD", "LOQ", "C_x", "Unit"],
                     rows, "Standard addition", text_cols=(0, 1, 8))
        pr = end + 2
        for path_, w in (("compare/ca_traces.png", 560), ("compare/calibrations.png", 540)):
            fig = figures.get(path_)
            if fig is not None:
                pr += _insert_png(wc, pr, 0, fig.png, path_.replace("/", "_"), w)
        index.append((wc.get_name(), "Comparison", f"{len(ca_runs)} runs"))

    # ---- summary sheet (first)
    summary.hide_gridlines(2)
    summary.set_column(0, 0, 34)
    summary.set_column(1, 1, 24)
    summary.set_column(2, 8, 18)
    summary.set_row(0, 38)
    summary.merge_range(0, 0, 0, 8, report.title, f.band)
    summary.merge_range(1, 0, 1, 8, "Electrochemical analysis workbook", f.band_sub)
    row = 3
    if report.meta_rows:
        row = _kv(summary, f, row, 0, report.meta_rows + [("Generated", f"{report.generated} · Echem Analyzer {report.version}")],
                  "Project") + 1
    else:
        row = _kv(summary, f, row, 0, [("Generated", f"{report.generated} · Echem Analyzer {report.version}")], "Project") + 1
    if data.meta.notes:
        summary.merge_range(row, 0, row, 6, data.meta.notes, f.note)
        summary.set_row(row, 15 * (1 + data.meta.notes.count("\n") + len(data.meta.notes) // 110))
        row += 2

    ca_rows = []
    for c, s, r in ca_runs:
        fit = r.fit
        ca_rows.append([f"{file_short(c.source)} · {curve_short_label(c)}", "drift" if s.method == "drift" else "plateau",
                        len(r.step_times), r.sensitivity if fit else "", fit.r2 if fit else "", r.lod if fit else "",
                        r.loq if fit else "", r.c_x if fit else "", s.stock_unit])
    if ca_rows:
        row = _table(summary, f, row, 0, ["Chronoamperometry run", "Method", "Steps", "Sensitivity (µA/unit)", "R²",
                                          "LOD", "LOQ", "C_x", "Unit"], ca_rows, "Standard addition", text_cols=(0, 1, 8)) + 1
    cv_rows = []
    for run, s in cv_runs:
        res = analyze_run(run, s)
        st = summarize_stability(res, s.stability_tol) if res else None
        last = res[-1] if res else None
        cv_rows.append([f"{file_short(run.source)} · {run.title}", f"{len(res)}/{len(run.cycles)}",
                        last.i_pa if last else "", last.i_pc if last else "",
                        st["i_pa"].last_change_pct if st and len(res) > 1 else "",
                        (f"cycle {st['i_pa'].stable_from}" if st and st["i_pa"].stable_from is not None
                         else ("not yet" if st and len(res) > 1 else ""))])
    if cv_rows:
        row = _table(summary, f, row, 0, ["Voltammetry run", "Cycles used", "Anodic ip (µA)", "Cathodic ip (µA)",
                                          "Last change (%)", "Anodic stable from"], cv_rows,
                     "Cyclic voltammetry", text_cols=(0, 1, 5)) + 1
    cal_table = next((b for sec in report.sections for b in sec.blocks
                      if isinstance(b, TableBlock) and b.caption == "Sensor study: calibrations"), None)
    if cal_table is not None:
        row = _table(summary, f, row, 0, cal_table.header, [[_cell(c) for c in r] for r in cal_table.rows],
                     "Sensor study: calibrations", text_cols=(0, 1, 2, 4, 5, 6, 7)) + 1
    if index:
        summary.write(row, 0, "CONTENTS", f.section)
        summary.write(row, 1, "", f.section)
        for i, (sheet, kind, label) in enumerate(index, start=1):
            summary.write_url(row + i, 0, f"internal:'{sheet}'!A1", f.link, sheet)
            summary.write(row + i, 1, kind, f.key)
            summary.write(row + i, 2, label, f.td_text)
        row += len(index) + 2
    summary.merge_range(row, 0, row, 6,
                        "Standard addition: the plateau |i| of each segment is fitted against the added concentration "
                        "C = Cs·ΣV / (V0+ΣV); the x-intercept gives Cx = intercept / slope. LOD = 3σ/S, LOQ = 10σ/S. "
                        "The data sheets hold the raw numbers; their charts are native Excel charts you can edit.", f.note)
    summary.set_row(row, 48)
    summary.activate()
    wb.close()
    return ["Summary"] + [n for n, _, _ in index]
