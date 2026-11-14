"""A one-page datasheet for each sensor variant, as a PDF (the Qt-free part builds a report; ``report_pdf.write_pdf`` writes it).

What a collaborator or a reviewer asks first: how the sensor was made, what it measures over what range, how sensitive and how
reproducible it is, how quickly it answers, how long it lasts and how many of the electrodes made are good.  Each page gives that for
one variant, with the pooled calibration beside the numbers.  Everything is read from the project (its responses, devices and
acceptance rules) by the same code the Study tab uses, so the page agrees with what is on screen.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Optional, Sequence

import numpy as np

from . import __version__
from . import acceptance as acc
from . import devices as dv
from . import plots
from . import study_analysis as sa
from . import variants as vr
from .models import ProjectData
from .report import FigureBlock, KeyValueBlock, NoteBlock, Report, Section


def _g(v: float, digits: int = 4, unit: str = "") -> str:
    return "—" if v is None or not math.isfinite(v) else f"{v:.{digits}g}{(' ' + unit) if unit else ''}"


def _fabrication_rows(devices: Sequence[dv.Device]) -> list[tuple[str, str]]:
    rows = [("Electrodes", f"{len(devices)}: " + ", ".join(d.id for d in devices))]
    lots = sorted({d.lot for d in devices if d.lot})
    dates = sorted({d.fabricated for d in devices if d.fabricated})
    if lots:
        rows.append(("Lot", ", ".join(lots)))
    if dates:
        rows.append(("Fabricated", ", ".join(dates)))
    names = []
    for d in devices:
        names += [n for n in d.params if n not in names]
    for n in names:
        values = [d.params[n] for d in devices if n in d.params and d.params[n] != ""]
        nums = [float(v) for v in values if dv.is_number(v)]
        if nums and len(nums) == len(values):
            sd = float(np.std(nums, ddof=1)) if len(nums) > 1 else float("nan")
            rows.append((n, f"{np.mean(nums):.4g}" + (f" ± {sd:.2g}" if math.isfinite(sd) and sd > 0 else "")))
        elif values:
            rows.append((n, ", ".join(dict.fromkeys(str(v) for v in values))))
    return rows


def _merits_rows(row: vr.VariantRow, cmp_: vr.Comparison) -> list[tuple[str, str]]:
    interval = f" (95 %: {_g(row.sens_lo, 3)} – {_g(row.sens_hi, 3)})" if math.isfinite(row.sens_lo) else ""
    out = [("Sensitivity, mean of electrodes", f"{_g(row.sens_mean, 4)} {cmp_.sensitivity_unit}{interval}".strip()),
           ("Spread between electrodes", f"{_g(row.sens_rsd, 3)} % RSD ({row.n_calibrated} calibrated)" if math.isfinite(row.sens_rsd) else "—"),
           ("Sensitivity, pooled calibration", f"{_g(row.pooled_sensitivity, 4)} {cmp_.sensitivity_unit}".strip()),
           ("Detection limit", _g(row.pooled_lod, 3, cmp_.conc_unit)),
           ("Linear range", f"{_g(row.range_low, 3)} – {_g(row.range_high, 3)} {cmp_.conc_unit}" if math.isfinite(row.range_low) else "—"),
           ("R² of the pooled calibration", _g(row.pooled_r2, 5)),
           ("Reproducibility between electrodes", _g(row.repro_rsd, 3, "% RSD")),
           ("Response time t90 (median)", _g(row.t90, 3, "s")),
           ("Retention at the last time point", _g(row.retention, 4, "%"))]
    if math.isfinite(row.yield_pct):
        out.append(("Acceptance yield", f"{row.yield_pct:.0f} % of {row.n_judged} judged (95 %: {row.yield_lo:.0f} – {row.yield_hi:.0f} %)"))
    return out


def build(data: ProjectData, quantity: str, condition: str = "analyte", variant_names: Optional[Sequence[str]] = None,
          conc_unit: str = "") -> Report:
    """A report with one datasheet page per variant (all of them, or the named ones).  Raises ValueError if there is nothing to show."""
    responses = list(data.responses)
    devices = dv.sync(dv.load(data.extras), responses)
    rules = acc.load(data.extras)
    cmp_ = vr.compare_variants(responses, devices, quantity, condition, rules, conc_unit)
    rows = [r for r in cmp_.rows if variant_names is None or r.variant in variant_names]
    if not rows or not cmp_.ok:
        raise ValueError("Nothing to put on a datasheet: " + (cmp_.warnings[0] if cmp_.warnings else f"no variant named {variant_names}."))
    sections = []
    for i, row in enumerate(rows):
        members = [devices[d] for d in row.devices if d in devices]
        sec = Section(f"Datasheet · {row.variant}", subtitle=f"{quantity} · {condition}", kind="study", page_break=i > 0)
        sec.blocks.append(KeyValueBlock(_fabrication_rows(members), "How it was made"))
        sec.blocks.append(KeyValueBlock(_merits_rows(row, cmp_), "Performance"))
        subset = [r for r in responses if r.group in row.devices]
        cs = sa.run_calibration(subset, sa.CalibrationOptions(quantity=quantity, condition=condition, model="linear", conc_unit=cmp_.conc_unit))
        if cs.ok:
            png = plots.render_png(lambda fig, p, cs=cs: plots.study_calibration(fig, cs, p), (6.4, 3.2))
            sec.blocks.append(FigureBlock(f"datasheet/{i}_calibration.png", f"Calibration of the {row.variant} electrodes (all standards pooled)",
                                          png, *plots.png_size(png), 0.46))              # small enough that a page holds it on A4 and on Letter
        concs = sorted({round(x, 9) for x in (cs.x.tolist() if cs.ok else [])})
        cond = [f"Response: {quantity} ({cmp_.y_unit})", f"condition: {condition}"]
        if concs:
            cond.append(f"standards from {_g(concs[0], 3)} to {_g(concs[-1], 3)} {cmp_.conc_unit} ({len(concs)} levels)")
        sec.blocks.append(NoteBlock("Measured as " + "; ".join(cond) + ". Sensitivity is the slope of a straight-line calibration; the detection limit is "
                                    f"kσ/S with k = {sa.CalibrationOptions().k_lod:g}, σ from the blank or the fit residuals. The between-electrode "
                                    "spread and the yield describe the electrodes made, not a single one."))
        if cs.ok and cs.warnings:                    # one line: the plot rings flagged standards, and a page has little room
            more = f" (and {len(cs.warnings) - 1} more in the study report)" if len(cs.warnings) > 1 else ""
            sec.blocks.append(NoteBlock(cs.warnings[0][:220] + ("…" if len(cs.warnings[0]) > 220 else "") + more, "warning"))
        sections.append(sec)
    return Report(title=f"Sensor datasheet · {quantity}", meta_rows=[(k, v) for k, v in (("Project", data.meta.name), ("Analyte", data.meta.analyte),
                                                                 ("Sample matrix", data.meta.matrix), ("Operator", data.meta.operator)) if v],
                  generated=datetime.now().strftime("%Y-%m-%d %H:%M"), version=__version__, sections=sections)
