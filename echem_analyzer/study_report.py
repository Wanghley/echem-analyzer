"""The sensor study, electrode characterisation and per-measurement analyses as report sections (no Qt).

Everything is recomputed from the project's responses, stored study settings (``extras["study"]``), electrode
settings (``extras["electrode"]``) and analyzer settings (``extras["analysis_params"]``), by the same engines the
tabs use, so a report always shows what the window showed.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, fields
from typing import Any, Callable, Optional

from . import calibration as cal
from . import electrode_study as estudy
from . import plots, techniques
from . import study_analysis as sa
from .analyzers import ANALYZERS, g
from .cv_analysis import group_cv_runs
from .labels import curve_run_label
from .models import CVSettings, Curve, ProjectData, Response
from .report import FigureBlock, KeyValueBlock, NoteBlock, Section, TableBlock, _cached
from .reporting import safe_name

MAX_PER_KIND = 6              # calibrations / reproducibility / stability / selectivity sections of each kind
MAX_ANALYSES = 40             # one block per analysed measurement
MAX_RESPONSE_ROWS = 150       # the PDF table; the workbook and the project CSV hold every row


# =============================================================================== computing the studies

@dataclass
class StudyResults:
    calibrations: list[sa.CalibrationStudy] = field(default_factory=list)
    repros: list[sa.ReproStudy] = field(default_factory=list)
    stabilities: list[sa.StabilityStudy] = field(default_factory=list)
    selectivities: list[sa.SelectivityStudy] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)           # studies that could not be computed, and why

    @property
    def empty(self) -> bool:
        return not (self.calibrations or self.repros or self.stabilities or self.selectivities)


def _conc_levels(rs: list[Response], quantity: str, condition: str) -> set[float]:
    levels: set[float] = set()
    for r in sa.select(rs, quantity, condition, ("standard",)):
        if r.conc is None:
            continue
        try:
            levels.add(round(sa.conc_to(r.conc, r.conc_unit, "M"), 15))
        except ValueError:
            continue
    return levels


def _first_stored_then_rest(quantities: list[str], stored_q: str) -> list[str]:
    return ([stored_q] if stored_q in quantities else []) + [q for q in quantities if q != stored_q]


_MODELS = ("auto", "linear", "loglinear", "michaelis", "hill")
_SIGMAS = ("auto", "blank", "residual")


def _clean_option(name: str, value, default):
    """A stored option if it has the right type and range, else the default (project files can be edited by hand)."""
    if name == "time":
        ok = value is None or value == sa.NO_TIME or (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value))
        return value if ok else default
    if name == "group":
        return value if value is None or isinstance(value, str) else default
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, float):
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0 else default
    if name == "model":
        return value if value in _MODELS else default
    if name == "sigma_from":
        return value if value in _SIGMAS else default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    return default


def _calibration_options(quantity: str, condition: str, stored: dict) -> sa.CalibrationOptions:
    same = stored.get("quantity") == quantity
    from .prefs import prefs
    k = float(prefs.get("lod_k"))                              # the LOD factor chosen in Settings (LOQ keeps the 10/3 ratio)
    base = sa.CalibrationOptions(quantity=quantity, condition=condition, k_lod=k, k_loq=k * 10.0 / 3.0)
    kw = {}
    for f in fields(sa.CalibrationOptions):
        if f.name in ("quantity", "condition"):
            continue
        kw[f.name] = _clean_option(f.name, stored[f.name], getattr(base, f.name)) if same and f.name in stored else getattr(base, f.name)
    return sa.CalibrationOptions(quantity=quantity, condition=condition, **kw)


def compute_study(responses: list[Response], extras: dict) -> StudyResults:
    st = extras.get("study") or {}
    out = StudyResults()
    quantities = sa.quantities(responses)

    # ---- calibration: one per quantity that has standards at two or more concentrations
    cal_st = st.get("calibration") or {}
    for q in _first_stored_then_rest(quantities, cal_st.get("quantity", "")):
        conds = [c for c in sa.conditions(responses, q) if len(_conc_levels(responses, q, c)) >= 2]
        if not conds:
            continue
        wanted = cal_st.get("condition") if cal_st.get("quantity") == q else None
        cond = wanted if wanted in conds else ("analyte" if "analyte" in conds else conds[0])
        try:
            cs = sa.run_calibration(responses, _calibration_options(q, cond, cal_st))
        except Exception as exc:  # noqa: BLE001 - one broken study must not stop the report
            out.errors.append(f"Calibration of {q}: {exc}")
            continue
        if cs.ok:
            out.calibrations.append(cs)
        if len(out.calibrations) >= MAX_PER_KIND:
            break

    # ---- reproducibility between electrodes
    rep_st = st.get("reproducibility") or {}
    for q in _first_stored_then_rest(quantities, rep_st.get("quantity", "")):
        same = rep_st.get("quantity") == q
        conds = sa.conditions(responses, q)
        cond = rep_st.get("condition") if same and rep_st.get("condition") in conds else ("analyte" if "analyte" in conds else (conds[0] if conds else "analyte"))
        try:
            rs = sa.run_reproducibility(responses, q, cond, time=_clean_option("time", rep_st.get("time"), None) if same else None)
        except Exception as exc:  # noqa: BLE001
            out.errors.append(f"Reproducibility of {q}: {exc}")
            continue
        if any(row.n_groups >= 2 for row in rs.rows):
            out.repros.append(rs)
        if len(out.repros) >= MAX_PER_KIND:
            break

    # ---- stability over time
    stab_st = st.get("stability") or {}
    for q in _first_stored_then_rest(quantities, stab_st.get("quantity", "")):
        same = stab_st.get("quantity") == q
        timed = [r for r in sa.usable(responses) if r.quantity == q and r.role == "standard" and r.time is not None]
        if len({r.time for r in timed}) < 2:
            continue
        conds = sorted({r.condition for r in timed})
        cond = stab_st.get("condition") if same and stab_st.get("condition") in conds else ("analyte" if "analyte" in conds else conds[0])
        conc, unit = None, ""
        if same and stab_st.get("conc"):
            try:
                a, b = str(stab_st["conc"]).split()
                conc, unit = float(a), b
            except ValueError:
                conc = None
        try:
            ref = stab_st.get("reference") if stab_st.get("reference") in ("first", "max") else "first"
            ss = sa.run_stability(responses, q, cond, conc, unit, _clean_option("group", stab_st.get("group") or None, None) if same else None,
                                  ref if same else "first")
        except Exception as exc:  # noqa: BLE001
            out.errors.append(f"Stability of {q}: {exc}")
            continue
        if ss.result is not None:
            out.stabilities.append(ss)
        if len(out.stabilities) >= MAX_PER_KIND:
            break

    # ---- selectivity: the analyte and at least one other condition
    sel_st = st.get("selectivity") or {}
    for q in _first_stored_then_rest(quantities, sel_st.get("quantity", "")):
        same = sel_st.get("quantity") == q
        conds = sa.conditions(responses, q)
        if len(conds) < 2:
            continue
        analyte = sel_st.get("analyte") if same and sel_st.get("analyte") in conds else ("analyte" if "analyte" in conds else conds[0])
        try:
            ss = sa.run_selectivity(responses, q, analyte)
        except Exception as exc:  # noqa: BLE001
            out.errors.append(f"Selectivity of {q}: {exc}")
            continue
        if ss.rows:
            out.selectivities.append(ss)
        if len(out.selectivities) >= MAX_PER_KIND:
            break
    return out


# =============================================================================== helpers

def _rkey(responses: list[Response], extras: dict) -> int:
    return hash(json.dumps([[r.to_dict() for r in responses], extras.get("study"), extras.get("electrode")],
                           sort_keys=True, default=str))


def _figure(path: str, caption: str, key: tuple, draw: Callable, size: tuple[float, float], fraction: float = 1.0) -> FigureBlock:
    png = _cached(key, lambda: plots.render_png(draw, size))
    return FigureBlock(path, caption, png, *plots.png_size(png), fraction)


def _fmt_time(t: Optional[float]) -> str:
    return "" if t is None else f"{t:g}"


def responses_table(responses: list[Response]) -> tuple[list[str], list[list[str]]]:
    header = ["Measurement", "Quantity", "Condition", "Electrode", "Role", "Used", "Value", "± SD", "Unit", "Conc.", "Time"]
    rows = [[r.source_label or r.id, r.quantity, r.condition, r.group, r.role, "yes" if r.use else "no", g(r.value, 5),
             g(r.sd, 3) if r.sd is not None else "", r.unit,
             f"{g(r.conc, 4)} {r.conc_unit}" if r.conc is not None else "", _fmt_time(r.time)] for r in responses]
    return header, rows


def _where(o: sa.CalibrationOptions) -> str:
    return " · ".join(x for x in (o.condition, f"electrode {o.group}" if o.group else "all electrodes",
                                  "all times" if o.time is None else ("no time set" if o.time == sa.NO_TIME else f"time = {g(o.time)}")))


def calibration_rows(cs: sa.CalibrationStudy) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(settings, results) key/value rows of a calibration."""
    o, f = cs.options, cs.fom
    settings = [("Quantity", f"{o.quantity} ({cs.y_unit})"), ("Selection", _where(o)),
                ("Model", cal.MODEL_LABELS[cs.model] + (" (chosen by AIC)" if o.model == "auto" else "")),
                ("Concentration unit", cs.conc_unit),
                ("σ for LOD / LOQ", {"auto": "blank if available, else residuals", "blank": "blank measurements",
                                     "residual": "fit residuals"}.get(o.sigma_from, o.sigma_from)),
                ("LOD / LOQ factors", f"{g(o.k_lod)} σ/S and {g(o.k_loq)} σ/S"),
                ("Linear range criterion", f"R² ≥ {g(o.r2_min)} and deviation ≤ {o.dev_max * 100:.0f} %" if o.restrict_range else "all points")]
    results = [("Standards", f"{len(cs.x)} points at {len(set(cs.x.tolist()))} concentrations"),
               ("Blank replicates", str(len(cs.blanks))),
               ("R²", g(cs.fit.r2, 5)),
               ("Sensitivity", f"{g(cs.sensitivity, 4)} {cs.sensitivity_unit}".strip()),
               ("LOD", f"{g(cs.lod, 3)} {cs.conc_unit}"), ("LOQ", f"{g(cs.loq, 3)} {cs.conc_unit}")]
    if f is not None:
        results.append(("Linear range", f"{g(f.range_low, 3)} – {g(f.range_high, 3)} {cs.conc_unit} ({f.n_range} levels)"
                        if f.n_range else "not established"))
        results.append(("σ used", f"{g(f.sigma, 3)} {cs.y_unit} ({f.sigma_source})" if math.isfinite(f.sigma) else "—"))
    return settings, results


def calibration_summary_table(studies: list[sa.CalibrationStudy]) -> TableBlock:
    rows = []
    for cs in studies:
        f = cs.fom
        rows.append([cs.options.quantity, cs.options.condition, cal.MODEL_LABELS[cs.model].split(" ")[0], g(cs.fit.r2, 4),
                     f"{g(cs.sensitivity, 4)} {cs.sensitivity_unit}".strip(), f"{g(cs.lod, 3)} {cs.conc_unit}",
                     f"{g(cs.loq, 3)} {cs.conc_unit}",
                     f"{g(f.range_low, 3)} – {g(f.range_high, 3)}" if f is not None and f.n_range else "—"])
    return TableBlock(["Quantity", "Condition", "Model", "R²", "Sensitivity", "LOD", "LOQ", "Linear range"], rows,
                      "Sensor study: calibrations", align_right_from=3)


# =============================================================================== sections

def _calibration_section(cs: sa.CalibrationStudy, key: int, with_figures: bool, index: int) -> Section:
    o = cs.options
    sec = Section(f"Calibration · {o.quantity}", subtitle=f"{o.condition} · {cs.conc_unit} · {cal.MODEL_LABELS[cs.model]}",
                  kind="study", page_break=True)
    settings, results = calibration_rows(cs)
    sec.data = cs
    sec.blocks += [KeyValueBlock(settings, "Settings"), KeyValueBlock(results, "Results")]
    for w in cs.warnings:
        sec.blocks.append(NoteBlock(w, "warning"))
    if with_figures:
        has_rec = cs.recovery is not None
        sec.blocks.append(_figure(f"study/calibration_{index}_{safe_name(o.quantity)}.png", "Calibration with residuals and linear range",
                                  ("study-cal", key, index), lambda fig, p: plots.study_calibration(fig, cs, p), (6.6, 4.8),
                                  0.56 if has_rec else 0.62))
        if has_rec:                                          # next to the calibration, so the page does not spill over
            sec.blocks.append(_figure(f"study/recovery_{index}_{safe_name(o.quantity)}.png", "Spike recovery",
                                      ("study-rec", key, index), lambda fig, p: plots.study_recovery(fig, cs, p), (5.6, 4.2), 0.42))
    rows = []
    for k, fit in cs.fits.items():
        if fit.ok:
            rows.append([cal.MODEL_LABELS[k] + (" ✓" if k == cs.model else ""), f"{fit.r2:.4f}", g(fit.adj_r2, 4), g(fit.rmse, 3), g(fit.aic, 4),
                         ", ".join(f"{p.name}={g(p.value, 4)}" for p in fit.params)])
        else:
            rows.append([cal.MODEL_LABELS[k], "—", "—", "—", "—", fit.message])
    sec.blocks.append(TableBlock(["Model", "R²", "adj R²", "RMSE", "AIC", "Parameters"], rows,
                                 "Model comparison (lower AIC is better)", align_right_from=1))
    if cs.samples:
        sec.blocks.append(TableBlock(
            ["Sample", "Response", f"Found ({cs.conc_unit})", f"Expected ({cs.conc_unit})", "Recovery"],
            [[s.label, g(s.response, 4), f"{g(s.conc, 4)} ± {g(s.se, 2)}" if math.isfinite(s.conc) else "outside range",
              g(s.expected, 4), f"{s.recovery_pct:.1f} %" if math.isfinite(s.recovery_pct) else "—"] for s in cs.samples],
            "Samples quantified with this calibration", align_right_from=1))
        if cs.recovery is not None:
            sec.blocks.append(NoteBlock(f"Mean recovery {cs.recovery.mean_pct:.1f} % (RSD {g(cs.recovery.rsd_pct, 3)} %)."))
    return sec


def _repro_section(rs: sa.ReproStudy, key: int, with_figures: bool, index: int) -> Section:
    sec = Section(f"Reproducibility · {rs.quantity}", subtitle=f"{rs.condition} · between electrodes", kind="study", page_break=index == 0)
    ss = rs.sensitivity_stats
    rsds = [r.stats.rsd_pct for r in rs.rows if math.isfinite(r.stats.rsd_pct)]
    kv = [("Electrodes (max per level)", str(max(r.n_groups for r in rs.rows))),
          ("Mean RSD across levels", f"{g(sum(rsds) / len(rsds), 3)} %" if rsds else "—")]
    if ss is not None:
        kv += [("Sensitivity per electrode", ", ".join(f"{k}: {g(v, 4)}" for k, v in rs.sensitivity.items())),
               ("Sensitivity RSD", f"{g(ss.rsd_pct, 3)} %"), ("Mean sensitivity", f"{g(ss.mean, 4)} {rs.y_unit}/{rs.conc_unit}")]
    sec.blocks.append(KeyValueBlock(kv, "Results"))
    for w in rs.warnings:
        sec.blocks.append(NoteBlock(w, "warning"))
    if with_figures:
        sec.blocks.append(_figure(f"study/reproducibility_{index}_{safe_name(rs.quantity)}.png", "Response per electrode and RSD per level",
                                  ("study-rep", key, index), lambda fig, p: plots.study_reproducibility(fig, rs, p), (7.4, 3.6), 0.85))
    sec.blocks.append(TableBlock(
        [f"Conc ({rs.conc_unit})", "Electrodes", "Points", "Mean", "SD", "RSD %", "± CI 95 %", "Outliers"],
        [[f"{r.conc:g}", str(r.n_groups), str(r.n_total), g(r.stats.mean, 4), g(r.stats.sd, 3), g(r.stats.rsd_pct, 3),
          g(r.stats.ci95, 3), ", ".join(r.outliers) or "—"] for r in rs.rows], "Per concentration level", align_right_from=1))
    return sec


def _stability_section(st: sa.StabilityStudy, key: int, with_figures: bool, index: int) -> Section:
    r = st.result
    sec = Section(f"Stability · {st.quantity}", subtitle=f"{st.condition}" + (f" · {g(st.conc)}" if st.conc is not None else ""),
                  kind="study", page_break=index == 0)
    sec.blocks.append(KeyValueBlock([("Retention at the last time point", f"{r.final_retention_pct:.1f} %"),
                                     ("Drift", f"{g(r.drift_pct_per_unit, 3)} % per time unit"),
                                     ("Half-life", g(r.half_life, 3)), ("Time to 90 %", g(r.t_90, 3))], "Results"))
    for w in st.warnings:
        sec.blocks.append(NoteBlock(w, "warning"))
    if with_figures:
        sec.blocks.append(_figure(f"study/stability_{index}_{safe_name(st.quantity)}.png", "Response and retention over time",
                                  ("study-stab", key, index), lambda fig, p: plots.study_stability(fig, st, p), (6.4, 3.8), 0.62))
    sec.blocks.append(TableBlock(["Time", f"Response ({st.y_unit})", "Retention %"],
                                 [[f"{t:g}", g(y, 5), f"{rp:.1f}"] for t, y, rp in zip(r.t, r.y, r.retention_pct)], "Measurements",
                                 align_right_from=0))
    return sec


def _selectivity_section(ss: sa.SelectivityStudy, key: int, with_figures: bool, index: int) -> Section:
    sec = Section(f"Selectivity · {ss.quantity}", subtitle=f"interferents against {ss.analyte} at {g(ss.analyte_conc, 4)} {ss.conc_unit}",
                  kind="study", page_break=index == 0)
    for w in ss.warnings:
        sec.blocks.append(NoteBlock(w, "warning"))
    if with_figures:
        sec.blocks.append(_figure(f"study/selectivity_{index}_{safe_name(ss.quantity)}.png", "Response to each interferent",
                                  ("study-sel", key, index), lambda fig, p: plots.study_selectivity(fig, ss, p), (6.4, 3.8), 0.62))
    sec.blocks.append(TableBlock(
        ["Interferent", f"Conc ({ss.conc_unit})", f"Response ({ss.y_unit})", "Change vs analyte %", "Interference % (equal conc)", "K (sensitivity ratio)"],
        [[r.interferent, g(r.conc, 4), g(r.response, 4), f"{r.change_pct:+.1f}" if math.isfinite(r.change_pct) else "—",
          g(r.interference_pct, 3), g(r.k_sel, 3)] for r in ss.rows], "Interference", align_right_from=1))
    return sec


def study_sections(data: ProjectData, with_figures: bool = True, results: Optional[StudyResults] = None) -> list[Section]:
    rs = list(data.responses)
    if not rs:
        return []
    results = results or compute_study(rs, data.extras)
    key = _rkey(rs, data.extras)
    head = Section("Sensor study", subtitle=f"{len(rs)} responses from {len({r.source_uid for r in rs})} measurements",
                   kind="study", page_break=True)
    head.blocks.append(NoteBlock("Every analysis sends the numbers that matter for the sensor (peak current, Rct, a plateau, a potential…) "
                                 "to this table. Calibration, reproducibility, stability, selectivity and recovery below are computed from it."))
    if not results.empty:
        if results.calibrations:
            head.blocks.append(calibration_summary_table(results.calibrations))
    for msg in results.errors:
        head.blocks.append(NoteBlock(f"Could not compute: {msg}", "warning"))
    header, rows = responses_table(rs)
    more = len(rows) - MAX_RESPONSE_ROWS
    head.blocks.append(TableBlock(header, rows[:MAX_RESPONSE_ROWS], "Responses", align_right_from=6))
    if more > 0:
        head.blocks.append(NoteBlock(f"{more} more responses are in the Excel workbook and in the project's results/responses.csv."))
    out = [head]
    out += [_calibration_section(cs, key, with_figures, i) for i, cs in enumerate(results.calibrations)]
    out += [_repro_section(r, key, with_figures, i) for i, r in enumerate(results.repros)]
    out += [_stability_section(s, key, with_figures, i) for i, s in enumerate(results.stabilities)]
    out += [_selectivity_section(s, key, with_figures, i) for i, s in enumerate(results.selectivities)]
    return out


# =============================================================================== electrode characterisation

def electrode_sections(data: ProjectData, with_figures: bool = True) -> list[Section]:
    runs = group_cv_runs(c for f in data.files for c in f.curves)
    if not runs:
        return []
    settings_of = lambda run: data.cv_settings.get(run.uid) or CVSettings(scan_rate=run.scan_rate)      # noqa: E731
    prm = estudy.params_from(data.extras)
    out: list[Section] = []
    sel = estudy.select_runs(runs, settings_of, estudy.state_from(data.extras, "scan_rate"))
    a, per_run = estudy.run_scan_rate(sel, prm)
    if a is not None:
        sec = Section("Electrode characterisation · scan-rate study", subtitle=f"{len(a.nu)} scan rates", kind="electrode", page_break=True)
        ec = [v for v in (a.ecsa_a, a.ecsa_c) if math.isfinite(v)]
        exp = [v for v in (a.loglog_a, a.loglog_c) if math.isfinite(v)]
        lv = a.laviron
        sec.blocks.append(KeyValueBlock([
            ("Redox probe", f"{g(prm.conc)} mM, n = {prm.n}"), ("Diffusion coefficient", f"{g(prm.D)} cm²/s"),
            ("Geometric area", f"{g(prm.area)} cm²" if prm.area > 0 else "not given"), ("Temperature", f"{g(prm.temp)} °C"),
            ("Runs used", str(len(a.nu)))], "Setup"))
        sec.blocks.append(KeyValueBlock([
            ("Electroactive area (ECSA)", f"{g(sum(ec) / len(ec), 4)} cm²" if ec else "needs concentration and D"),
            ("Roughness factor", g(a.roughness, 3) if math.isfinite(a.roughness) else "needs the geometric area"),
            ("log ip – log ν slope", f"{g(sum(exp) / len(exp), 3)} ({a.regime})" if exp else "—"),
            ("ip vs √ν slope, anodic / cathodic", f"{g(a.fit_a.p('slope'), 4) if a.fit_a.ok else '—'} / {g(a.fit_c.p('slope'), 4) if a.fit_c.ok else '—'} µA/(V/s)½"),
            ("R², anodic / cathodic", f"{g(a.fit_a.r2, 5) if a.fit_a.ok else '—'} / {g(a.fit_c.r2, 5) if a.fit_c.ok else '—'}"),
            ("Laviron α · ks", f"{lv.alpha:.2f} · {lv.ks:.3g} s⁻¹" if lv and math.isfinite(lv.alpha) else "not applicable (needs ΔEp > 200/n mV)")], "Results"))
        for w in a.warnings:
            sec.blocks.append(NoteBlock(w, "warning"))
        key = hash(json.dumps([a.nu.tolist(), a.ip_a.tolist(), a.ip_c.tolist(), prm.__dict__], default=str))
        if with_figures:
            sec.blocks.append(_figure("electrode/scan_rate.png", "Randles–Sevcik, scan-rate exponent, Laviron and peak separation",
                                      ("el-sr", key), lambda fig, p: plots.electrode_scan_rate(fig, a, p), (7.4, 5.0), 0.8))
        sec.blocks.append(TableBlock(["ν (V/s)", "ip a (µA)", "ip c (µA)", "Ep a (V)", "Ep c (V)", "ΔEp (mV)"],
                                     [[g(float(n), 4), g(float(x), 4), g(float(y), 4), g(float(u), 4), g(float(v), 4), g(float(d) * 1000, 4)]
                                      for n, x, y, u, v, d in zip(a.nu, a.ip_a, a.ip_c, a.ep_a, a.ep_c, a.delta_ep)],
                                     "Peaks per scan rate", align_right_from=0))
        out.append(sec)
    sel = estudy.select_runs(runs, settings_of, estudy.state_from(data.extras, "cdl"))
    nu, di, o, _ = estudy.run_cdl(sel, prm)
    if o:
        sec = Section("Electrode characterisation · double-layer capacitance", subtitle=f"{len(nu)} scan rates at E = {g(prm.e_mid)} V",
                      kind="electrode", page_break=not out)
        sec.blocks.append(KeyValueBlock([
            ("Cdl", f"{g(o['cdl_uF'], 4)} ± {g(o['cdl_se'], 2)} µF"), ("Cdl per geometric area", f"{g(o['per_area'], 4)} µF/cm²"),
            ("Roughness (Cdl / specific capacitance)", g(o["roughness"], 3)), ("Linearity R²", g(o["r2"], 4))], "Results"))
        if with_figures:
            key = hash(json.dumps([nu.tolist(), di.tolist()], default=str))
            sec.blocks.append(_figure("electrode/cdl.png", "Half the anodic–cathodic current gap against scan rate",
                                      ("el-cdl", key), lambda fig, p: plots.electrode_cdl(fig, nu, di, o, p), (5.8, 4.0), 0.55))
        out.append(sec)
    return out


# =============================================================================== per-measurement analyses

def format_param(f, value: Any) -> str:
    if f.kind == "bool":
        return "yes" if value else "no"
    if f.kind == "choice":
        return next((label for label, v in f.choices if v == value), str(value))
    return f"{g(value, 5)} {f.suffix}".strip()


def analysis_sections(data: ProjectData, with_figures: bool = True) -> list[Section]:
    curves: dict[str, Curve] = {c.uid: c for f in data.files for c in f.curves}
    order = {uid: i for i, uid in enumerate(curves)}
    pairs, seen = [], set()
    for r in data.responses:
        k = (r.source_uid, r.analyzer)
        if k in seen or r.source_uid not in curves or r.analyzer not in ANALYZERS:
            continue
        seen.add(k)
        pairs.append(k)
    keys = list(ANALYZERS)
    pairs.sort(key=lambda k: (keys.index(k[1]), order[k[0]]))
    out: list[Section] = []
    seen_per_analyzer: dict[str, int] = {}
    for uid, akey in pairs[:MAX_ANALYSES]:
        c, an = curves[uid], ANALYZERS[akey]
        params = an.params_for((data.extras.get("analysis_params") or {}).get(f"{akey}|{uid}"))
        res = an.run(c, params)
        k = seen_per_analyzer.get(akey, 0)
        seen_per_analyzer[akey] = k + 1
        # two measurements fill a page; breaking before every other one keeps a block from being split across pages
        sec = Section(curve_run_label(c), subtitle=f"{an.title} · {techniques.label(c.kind)}", kind="analysis", page_break=k % 2 == 0)
        sec.data = {"analyzer": akey, "uid": uid}
        sec.blocks.append(KeyValueBlock([(f.label, format_param(f, params[f.key])) for f in an.fields], "Settings"))
        sec.blocks.append(KeyValueBlock([(n, f"{v} {u}".strip()) for n, v, u in res.rows] if res.ok else [("Result", "analysis failed")], "Results"))
        for w in res.warnings:
            sec.blocks.append(NoteBlock(w, "warning"))
        if with_figures:
            key = (c.uid, len(c.x), float(c.y[0]) if c.n else 0.0, float(c.y.sum()) if c.n else 0.0, akey, json.dumps(params, sort_keys=True, default=str))
            sec.blocks.append(_figure(f"analysis/{akey}__{safe_name(c.uid)}.png", "", ("analysis",) + key,
                                      lambda fig, p, c=c, an=an, params=params, res=res: an.draw(fig, c, params, res, p),
                                      an.figsize, 0.62))
        if res.table:
            sec.blocks.append(TableBlock(res.table[0], res.table[1], "", align_right_from=1))
        out.append(sec)
    if len(pairs) > MAX_ANALYSES and out:
        out[-1].blocks.append(NoteBlock(f"{len(pairs) - MAX_ANALYSES} further analyses are not drawn here; their numbers are in the study table."))
    return out
