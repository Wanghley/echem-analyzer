"""Formatting and CSV/JSON export of CA analysis results (no GUI imports)."""
from __future__ import annotations

import csv
import io
import json
import math
import re
from pathlib import Path


from .cv_analysis import CVRun, analyze_run, summarize_stability
from .models import CAAnalysisSettings, CAResult, CVSettings, Curve


def g(x: float, digits: int = 4) -> str:
    """Compact general-format number; em dash for NaN / None."""
    if x is None or not math.isfinite(x):
        return "—"
    return f"{x:.{digits}g}"


def pm(x: float, se: float, digits: int = 4) -> str:
    if x is None or not math.isfinite(x):
        return "—"
    if se is None or not math.isfinite(se):
        return g(x, digits)
    return f"{g(x, digits)} ± {g(se, 2)}"


def summary_rows(s: CAAnalysisSettings, r: CAResult) -> list[tuple[str, str, str]]:
    """(name, value, unit) rows describing the fit and the derived figures of merit."""
    u = s.stock_unit
    f = r.fit
    rows: list[tuple[str, str, str]] = [
        ("Response method", "Drift-corrected" if s.method == "drift" else "Plateau mean", ""),
        ("Additions", str(len(r.step_times)), ""),
    ]
    if f is None:
        return rows
    rows += [
        ("Points in fit", str(f.n), ""),
        ("Slope = sensitivity", pm(f.slope, f.slope_se), f"µA/{u}"),
        ("Intercept", pm(f.intercept, f.intercept_se), "µA"),
        ("R²", g(f.r2, 5), ""),
        ("s(y/x)", g(f.s_yx), "µA"),
    ]
    if s.area_cm2 > 0:
        rows.append(("Sensitivity / area", g(r.sensitivity_area), f"µA {u}⁻¹ cm⁻²"))
    rows += [
        ("σ (" + ("fit residuals" if s.sigma_source == "fit" else "baseline noise") + ")", g(r.sigma), "µA"),
        ("LOD = 3σ/S", g(r.lod), u),
        ("LOQ = 10σ/S", g(r.loq), u),
        ("C_x (x-intercept)", pm(r.c_x, r.c_x_se), u),
    ]
    if s.v0_ml > 0:
        rows.append(("C_sample", g(r.c_sample), u))
    return rows


def results_plain_text(title: str, s: CAAnalysisSettings, r: CAResult) -> str:
    lines = [title] if title else []
    for name, val, unit in summary_rows(s, r):
        lines.append(f"{name}: {val} {unit}".rstrip())
    for w in r.warnings:
        lines.append(f"! {w}")
    return "\n".join(lines)


def step_table(s: CAAnalysisSettings, r: CAResult) -> tuple[list[str], list[list]]:
    u = s.stock_unit
    header = [
        "segment", "included", "t_step_s", "V_add_uL", f"C_added_{u}", f"C_override_{u}",
        "I_plateau_uA", "I_plateau_sd_uA", "delta_I_uA", "signal_uA",
    ]
    rows = []
    for k in range(len(r.plateau)):
        if k == 0:
            rows.append([0, bool(r.included[0]), "", "", 0.0, "", r.plateau[0], r.plateau_sd[0], 0.0, r.signal[0]])
        else:
            ov = s.step_conc_override[k - 1]
            rows.append([
                k, bool(r.included[k]), r.step_times[k - 1], s.step_volumes_ul[k - 1],
                r.conc_added[k], "" if ov is None else ov,
                r.plateau[k], r.plateau_sd[k], r.delta[k], r.signal[k],
            ])
    return header, rows


def safe_name(text: str) -> str:
    return re.sub(r"[^\w.+\-µ]+", "_", text).strip("_")


def analysis_dir_for(curve: Curve) -> Path:
    src = Path(curve.source)
    run = curve.meta.get("run_title") or curve.title
    return src.parent / "analysis" / safe_name(f"{src.stem}__{run}")


def write_exports(
    out_dir: Path, curve: Curve, s: CAAnalysisSettings, r: CAResult
) -> list[Path]:
    """Write steps.csv, results.csv and settings.json (figures are saved by the GUI)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    header, rows = step_table(s, r)
    p = out_dir / "steps.csv"
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    written.append(p)

    p = out_dir / "results.csv"
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["quantity", "value", "unit"])
        w.writerow(["file", Path(curve.source).name, ""])
        w.writerow(["run", curve.label, ""])
        for name, val, unit in summary_rows(s, r):
            w.writerow([name, val, unit])
        for msg in r.warnings:
            w.writerow(["warning", msg, ""])
    written.append(p)

    p = out_dir / "settings.json"
    p.write_text(json.dumps(s.to_dict(), indent=2), encoding="utf-8")
    written.append(p)
    return written


def load_saved_settings(curve: Curve) -> CAAnalysisSettings | None:
    p = analysis_dir_for(curve) / "settings.json"
    if not p.is_file():
        return None
    try:
        return CAAnalysisSettings.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def write_overlay_csv(path: Path, curves: list[Curve]) -> None:
    """Side-by-side x/y columns for any set of curves (ragged lengths padded blank)."""
    n = max((c.n for c in curves), default=0)
    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(sum(([f"{c.label} [{Path(c.source).name}]", ""] for c in curves), []))
        w.writerow(sum(([c.x_unit, c.y_unit] for c in curves), []))
        for k in range(n):
            row = []
            for c in curves:
                row += [repr(float(c.x[k])), repr(float(c.y[k]))] if k < c.n else ["", ""]
            w.writerow(row)


# --------------------------------------------------------------------------- CV (multi-cycle)

CV_HEADER = [
    "cycle", "included", "E_pa_V", "i_pa_uA", "E_pc_V", "i_pc_uA", "E_half_V", "dEp_V",
    "ip_ratio", "Q_anodic_uC", "Q_cathodic_uC", "C_apparent_uF", "i_pa_change_pct",
]


def cv_cycle_table(run: CVRun, s: CVSettings) -> tuple[list[str], list[list]]:
    """One row per cycle (excluded ones are listed too, flagged ``included = False``)."""
    every = analyze_run(run, s, include_excluded=True)
    kept = [r for r in every if r.cycle not in s.excluded_cycles]
    st = summarize_stability(kept, s.stability_tol)["i_pa"]
    change = dict(zip(st.cycles, st.rel_change_pct))
    rows = []
    for r in every:
        rows.append([
            r.cycle, r.cycle not in s.excluded_cycles, r.e_pa, r.i_pa, r.e_pc, r.i_pc, r.e_half,
            r.delta_ep, r.ip_ratio, r.q_anodic, r.q_cathodic, r.capacitance,
            change.get(r.cycle, float("nan")),
        ])
    return CV_HEADER, rows


def cv_summary_rows(run: CVRun, s: CVSettings) -> list[tuple[str, str, str]]:
    """(name, value, unit) rows: how far the cycles still change."""
    res = analyze_run(run, s)
    rows: list[tuple[str, str, str]] = [("Cycles analysed", str(len(res)), "")]
    if not res:
        return rows
    st = summarize_stability(res, s.stability_tol)
    for key, name in (("i_pa", "Anodic ip"), ("i_pc", "Cathodic ip")):
        t = st[key]
        rows.append((f"{name}, last cycle", g(t.values[-1], 4), "µA"))
        if len(res) >= 2:
            rows.append((f"{name}, last change", g(t.last_change_pct, 3), "%"))
            rows.append((f"{name}, total drift", g(t.total_drift_pct, 3), "%"))
            rows.append((f"{name}, stable from", f"cycle {t.stable_from}" if t.stable_from is not None else "not yet", ""))
    last = res[-1]
    if math.isfinite(last.capacitance):
        rows.append(("C apparent, last cycle", g(last.capacitance, 4), "µF"))
    return rows
    st = summarize_stability(res, s.stability_tol)
    for key, name in (("i_pa", "Anodic peak"), ("i_pc", "Cathodic peak")):
        t = st[key]
        rows.append((f"{name}, last cycle", g(t.values[-1], 4), "µA"))
        if len(res) >= 2:
            rows.append((f"{name}, change in last cycle", g(t.last_change_pct, 3), "%"))
            rows.append((f"{name}, drift first→last", g(t.total_drift_pct, 3), "%"))
            verdict = (f"cycle {t.stable_from}" if t.stable_from is not None
                       else f"not yet (still changing > {g(s.stability_tol, 3)} %/cycle)")
            rows.append((f"{name}, stable from", verdict, ""))
    last = res[-1]
    if math.isfinite(last.capacitance):
        rows.append(("Apparent capacitance, last cycle", g(last.capacitance, 4), "µF"))
    return rows


# --------------------------------------------------------------------------- CSV helpers

def cell(x) -> object:
    """CSV cell for a number: blank for NaN/inf so spreadsheets and pandas read it cleanly."""
    if isinstance(x, (bool, str)) or x is None:
        return x
    try:
        v = float(x)
    except (TypeError, ValueError):
        return x
    if not math.isfinite(v):
        return ""
    return int(v) if v.is_integer() else repr(v)


def csv_text(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow([cell(c) for c in r])
    return buf.getvalue()


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    Path(path).write_text(csv_text(header, rows), encoding="utf-8", newline="")
