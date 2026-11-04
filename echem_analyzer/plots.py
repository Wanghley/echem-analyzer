"""Every figure the app draws, as plain matplotlib functions (no Qt, no GUI state).

The live window, the project snapshot, the PDF report and the Excel workbook all call these, so they
always show identical plots.  Each function takes the palette to draw with; exports use ``LIGHT``.
"""
from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Callable, Optional, Sequence

import matplotlib
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator
from scipy.ndimage import uniform_filter1d

from .cv_analysis import CVRun, summarize_stability, sweep_masks, window_baselines
from .labels import file_short
from .models import KIND_CV, CAAnalysisSettings, CAResult, CVCycleResult, CVSettings, Curve
from .palette import Palette, mpl_rc, report_palette
from .reporting import g, pm

CYCLE_SKIP_FRAC = 0.05            # leading fraction of a cycle ignored by the y auto-zoom only


# --------------------------------------------------------------------------- helpers

def style_axes(ax, p: Palette) -> None:
    """(Re)apply the palette to an axes that was cleared rather than created.

    matplotlib re-applies the facecolor / spine colours an axes was *created* with when it is cleared, so
    after a light/dark switch a cleared axes would keep the old theme without this.
    """
    ax.set_axis_on()
    ax.set_facecolor(p.plot_bg)
    for sp in ax.spines.values():
        sp.set_edgecolor(p.border_strong)
    rc = matplotlib.rcParams                                  # the active plot style: modern or publication
    ink = p.text if not rc["axes.grid"] else p.muted
    for sp in ax.spines.values():
        sp.set_edgecolor(p.text if not rc["axes.grid"] else p.border_strong)
    for name in ("top", "right"):
        ax.spines[name].set_visible(bool(rc[f"axes.spines.{name}"]))
    ax.tick_params(which="both", colors=ink, labelcolor=ink, direction=rc["xtick.direction"],
                   top=bool(rc["xtick.top"]), right=bool(rc["ytick.right"]))
    if rc["xtick.minor.visible"]:
        ax.minorticks_on()
    ax.xaxis.label.set_color(ink)
    ax.yaxis.label.set_color(ink)
    if rc["axes.grid"]:
        ax.grid(True, color=p.grid, linewidth=0.8)
    else:
        ax.grid(False)
    ax.set_axisbelow(True)                                   # grid lines behind bars and patches, not over them


def _note(ax, text: str, p: Palette) -> None:
    ax.text(0.5, 0.5, text, ha="center", va="center", transform=ax.transAxes, color=p.muted, fontsize=10)


def _alpha(p: Palette, light: float, dark: float) -> float:
    return dark if p.name == "dark" else light


def cycle_color(k: int, n: int, p: Palette):
    """Sequential colour for the k-th of n cycles: the evolution reads from early to late."""
    cmap = matplotlib.colormaps["viridis"]
    t = k / max(1, n - 1)
    return cmap(0.35 + 0.65 * t) if p.name == "dark" else cmap(0.02 + 0.85 * t)


def sweep_segments(e: np.ndarray) -> list[tuple[int, int, bool]]:
    """Index ranges (inclusive end so lines join at the vertex) of constant sweep direction."""
    up, _ = sweep_masks(e)
    segs, start = [], 0
    for k in range(1, len(e)):
        if up[k] != up[k - 1]:
            segs.append((start, k, bool(up[start])))
            start = k
    segs.append((start, len(e) - 1, bool(up[start])))
    return segs


# --------------------------------------------------------------------------- chronoamperometry

def ca_trace(ax, curve: Curve, s: CAAnalysisSettings, r: CAResult, p: Palette, smooth: int = 1,
             title: str = "") -> None:
    """Current vs time with the detected steps, averaging windows and plateau levels."""
    ax.clear()
    style_axes(ax, p)
    n = len(curve.x) - s.drop_last if s.drop_last > 0 else len(curve.x)
    t, y = curve.x[:n], curve.y[:n]
    ax.plot(t, y, color=p.trace, lw=1.1, zorder=3)
    if s.drop_last > 0:
        ax.plot(curve.x[n:], curve.y[n:], "x", color=p.dropped, ms=5, zorder=3)
    if smooth > 2:
        ax.plot(t, uniform_filter1d(y, smooth, mode="nearest"), color=p.smooth, lw=1.4, zorder=4)

    for k, (w0, w1) in enumerate(r.win_bounds):
        ax.axvspan(w0, w1, color=p.window, alpha=_alpha(p, 0.17, 0.22), lw=0, zorder=1)
        if np.isfinite(r.plateau[k]):
            ax.hlines(r.plateau[k], w0, w1, color=p.plateau, lw=2.4, zorder=5)
    for j, ts in enumerate(r.step_times):
        ax.axvline(ts, color=p.step, lw=1.3, ls="--", zorder=2)
        ax.annotate(str(j + 1), (ts, 1.0), xycoords=("data", "axes fraction"), xytext=(4, -4),
                    textcoords="offset points", va="top", ha="left", color=p.step, fontsize=8.5, weight="bold")
    for ts in s.ignored_events:           # opposite-direction jumps: not additions, but flagged
        ax.axvline(ts, color=p.dropped, lw=1.4, ls=":", zorder=2)
        ax.annotate("!", (ts, 1.0), xycoords=("data", "axes fraction"), xytext=(4, -4),
                    textcoords="offset points", va="top", ha="left", color=p.warning,
                    fontsize=11, weight="bold")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel(f"Current ({curve.y_unit})")
    if title:
        ax.set_title(title, loc="left", color=p.text)


def ca_calibration(fig, s: CAAnalysisSettings, r: CAResult, p: Palette) -> None:
    """Signal vs added concentration with the fit, the x-intercept and a residual panel."""
    fig.clear()
    conc = r.conc_added
    have_conc = bool(np.all(np.isfinite(conc)) and np.ptp(conc) > 0)
    x = conc if have_conc else np.arange(len(conc), dtype=float)
    inc = r.included
    fit = r.fit if have_conc else None

    if fit is not None:
        gs = fig.add_gridspec(2, 1, height_ratios=[4, 1.1])
        ax = fig.add_subplot(gs[0])
        axr = fig.add_subplot(gs[1], sharex=ax)
    else:
        ax, axr = fig.add_subplot(111), None

    ax.plot(x[inc], r.signal[inc], "o", color=p.trace, ms=6.5, zorder=4)
    if (~inc).any():
        ax.plot(x[~inc], r.signal[~inc], "o", mfc="none", mec=p.dropped, mew=1.4, ms=6.5, zorder=4)
    ax.axhline(0, color=p.border_strong, lw=0.9)

    if fit is not None:
        x_lo = float(x[inc].min()) if inc.any() else 0.0
        x_hi = float(np.nanmax(x))
        xs = np.array([x_lo, x_hi])
        ax.plot(xs, fit.slope * xs + fit.intercept, color=p.fit, lw=1.6, zorder=3)
        xi = fit.x_intercept
        if np.isfinite(xi):
            if xi < x_lo:
                xe = np.array([xi, x_lo])
                ax.plot(xe, fit.slope * xe + fit.intercept, color=p.extrap, lw=1.3, ls="--", zorder=2)
            ax.plot([xi], [0], "D", color=p.cx, ms=6.5, zorder=5)
        ax.text(0.03, 0.97, f"C$_x$ = {pm(r.c_x, r.c_x_ci95, 3)} {s.stock_unit}", transform=ax.transAxes, ha="left",
                va="top", fontsize=10, weight="bold", color=p.cx)
        ax.text(0.03, 0.87, f"(± 95 % CI)\ny = {fit.slope:.4g}·x + {fit.intercept:.4g}\nR² = {fit.r2:.4f}",
                transform=ax.transAxes, ha="left", va="top", fontsize=8.5, color=p.muted)
        axr.axhline(0, color=p.border_strong, lw=0.9)
        axr.plot(fit.x, fit.residuals, "o", color=p.trace, ms=4)
        axr.set_ylabel("Resid. (µA)")
        axr.set_xlabel(f"Added concentration ({s.stock_unit})")
        ax.tick_params(labelbottom=False)
    else:
        ax.set_xlabel("Addition number (enter stock, $V_0$ and V add to calibrate)")
        ax.set_xticks(x)
    ax.set_ylabel("Signal (µA)")
    ax.set_title("Standard addition", loc="left", color=p.text)


# --------------------------------------------------------------------------- cyclic voltammetry

def cv_cycles(ax, run: CVRun, s: CVSettings, results: Sequence[CVCycleResult], p: Palette, branches: bool = False,
              peaks: bool = True, show_excluded: bool = True, zoom: bool = True, title: str = "") -> None:
    """The cycles of one run, light to dark, with peak markers, search windows and baselines."""
    ax.clear()
    style_axes(ax, p)
    used = [n for n in run.cycle_numbers if n not in s.excluded_cycles]
    skipped = [n for n in run.cycle_numbers if n in s.excluded_cycles]
    if show_excluded:
        for n in skipped:
            c = run.cycles[n]
            ax.plot(c.x, c.y, color=p.excluded, lw=1.0, ls=":", zorder=1)

    by_cycle = {r.cycle: r for r in results}
    for k, n in enumerate(used):
        c = run.cycles[n]
        col = cycle_color(k, len(used), p)
        label = f"cycle {n}" if (len(used) <= 6 or k in (0, len(used) - 1)) else None
        if branches:
            first = True
            for a, b, up in sweep_segments(c.x):
                ax.plot(c.x[a:b + 1], c.y[a:b + 1], color=col, lw=1.4, ls="-" if up else "--", zorder=3,
                        label=label if first else None)
                first = False
        else:
            ax.plot(c.x, c.y, color=col, lw=1.4, zorder=3, label=label)
        res = by_cycle.get(n)
        if res is not None and peaks:
            if np.isfinite(res.e_pa):
                ax.plot(res.e_pa, res.i_pa_raw, "^", color=col, mec=p.text, mew=0.8, ms=7, zorder=5)
            if np.isfinite(res.e_pc):
                ax.plot(res.e_pc, res.i_pc_raw, "v", color=col, mec=p.text, mew=0.8, ms=7, zorder=5)

    if s.anodic_window:
        ax.axvspan(*sorted(s.anodic_window), color=p.step, alpha=_alpha(p, 0.09, 0.12), lw=0, zorder=0)
    if s.cathodic_window:
        ax.axvspan(*sorted(s.cathodic_window), color=p.trace, alpha=_alpha(p, 0.09, 0.12), lw=0, zorder=0)
    if used and s.baseline == "linear":                      # draw the baselines of the last cycle used
        c = run.cycles[used[-1]]
        for seg in window_baselines(c.x, c.y, s).values():
            if seg:
                ax.plot([seg[0][0], seg[1][0]], [seg[0][1], seg[1][1]], color=p.text, lw=1.0, ls=":", zorder=4)

    ax.set_xlabel("Potential (V)")
    ax.set_ylabel("Current (µA)")
    if title:
        ax.set_title(title, loc="left", color=p.text)
    if used:
        ax.legend(loc="best")
        if zoom:
            ys = np.concatenate([run.cycles[n].y[int(len(run.cycles[n].y) * CYCLE_SKIP_FRAC):] for n in used])
            lo, hi = float(ys.min()), float(ys.max())
            pad = 0.08 * (hi - lo or 1.0)
            ax.set_ylim(lo - pad, hi + pad)
    else:
        _note(ax, "All cycles are unticked", p)


def cv_trends(fig, results: Sequence[CVCycleResult], s: CVSettings, p: Palette) -> None:
    """Peak current and charge per cycle: shows whether the electrode is conditioned."""
    fig.clear()
    ax1 = fig.add_subplot(2, 1, 1)
    ax2 = fig.add_subplot(2, 1, 2, sharex=ax1)
    if not results:
        _note(ax1, "No cycles selected", p)
        return
    cyc = [r.cycle for r in results]
    st = summarize_stability(results, s.stability_tol)

    ax1.plot(cyc, [r.i_pa for r in results], "o-", color=p.trace, label="anodic peak")
    ax1.plot(cyc, [r.i_pc for r in results], "s-", color=p.cx, label="cathodic peak")
    ax1.axhline(0, color=p.border_strong, lw=0.9)
    for key, col, name in (("i_pa", p.trace, "anodic"), ("i_pc", p.cx, "cathodic")):
        sf = st[key].stable_from
        if sf is not None:
            ax1.axvline(sf, color=col, lw=1.0, ls="--", alpha=0.6, label=f"{name} stable from cycle {sf}")
    ax1.set_ylabel("Peak current (µA)")
    ax1.set_title("Peak current per cycle", loc="left", color=p.text)
    ax1.legend(loc="best")
    ax1.tick_params(labelbottom=False)

    if s.scan_rate > 0:
        ax2.plot(cyc, [r.q_anodic for r in results], "o-", color=p.trace, label="Q anodic")
        ax2.plot(cyc, [-r.q_cathodic for r in results], "s-", color=p.cx, label="|Q cathodic|")
        ax2.set_ylabel("Charge (µC)")
        ax3 = ax2.twinx()
        ax3.spines["right"].set_visible(True)
        ax3.plot(cyc, [r.capacitance for r in results], "d--", color=p.extrap, label="C apparent")
        ax3.set_ylabel("C apparent (µF)", color=p.extrap)
        ax3.grid(False)
        h1, l1 = ax2.get_legend_handles_labels()
        h2, l2 = ax3.get_legend_handles_labels()
        ax2.legend(h1 + h2, l1 + l2, loc="best")
    else:
        _note(ax2, "Enter the scan rate\nto get charge and capacitance", p)
    ax2.set_title("Charge per cycle", loc="left", color=p.text)
    ax2.set_xlabel("Cycle")
    ax2.xaxis.set_major_locator(MaxNLocator(integer=True))


def cv_overlay(ax, curves: Sequence[Curve], p: Palette, by_file: bool = False, files: Sequence[str] = (),
               zoom: bool = True, label_fn: Optional[Callable[[Curve], str]] = None) -> None:
    ax.clear()
    style_axes(ax, p)
    styles = ["-", "--", ":", "-."]
    if not curves:
        _note(ax, "Tick one or more curves on the left", p)
        return
    for n, c in enumerate(curves):
        fname = Path(c.source).name
        fi = list(files).index(fname) if fname in files else 0
        si = (c.scan or (n + 1)) - 1
        col = p.series[(fi if by_file else si) % len(p.series)]
        ls = styles[(si if by_file else fi) % len(styles)]
        ax.plot(c.x, c.y, color=col, ls=ls, lw=1.3, label=label_fn(c) if label_fn else c.title)
    all_cv = all(c.kind == KIND_CV for c in curves)
    c0 = curves[0]
    ax.set_xlabel(f"Potential E ({c0.x_unit})" if all_cv else f"x ({c0.x_unit})")
    ax.set_ylabel(f"Current i ({c0.y_unit})" if all_cv else f"y ({c0.y_unit})")
    if len(curves) <= 14:
        ax.legend(fontsize=7.5, loc="best", ncols=2 if len(curves) > 7 else 1)
    if zoom:
        ys = np.concatenate([c.y[int(len(c.y) * CYCLE_SKIP_FRAC):] for c in curves])
        if ys.size:
            lo, hi = float(ys.min()), float(ys.max())
            pad = 0.08 * (hi - lo or 1.0)
            ax.set_ylim(lo - pad, hi + pad)


# --------------------------------------------------------------------------- comparison

def compare_traces(ax, runs: Sequence[tuple[Curve, CAAnalysisSettings, CAResult]], p: Palette,
                   subtract_baseline: bool, align: bool, label_fn: Callable[[Curve], str]) -> None:
    ax.clear()
    style_axes(ax, p)
    for n, (c, s, r) in enumerate(runs):
        m = len(c.x) - s.drop_last if s.drop_last > 0 else len(c.x)
        t, y = c.x[:m].copy(), c.y[:m].copy()
        if align and len(r.step_times):
            t = t - r.step_times[0]
        if subtract_baseline and np.isfinite(r.plateau[0]):
            y = y - r.plateau[0]
        ax.plot(t, y, color=p.series[n % len(p.series)], lw=1.2, label=label_fn(c))
    ax.set_xlabel("Time since first addition (s)" if align else "Time (s)")
    ax.set_ylabel("Current − baseline (µA)" if subtract_baseline else "Current (µA)")
    ax.set_title("Chronoamperograms", loc="left", color=p.text)
    if runs:
        ax.legend(loc="best", fontsize=8)
    else:
        _note(ax, "Tick CA runs on the left", p)


def compare_calibrations(ax, runs: Sequence[tuple[Curve, CAAnalysisSettings, CAResult]], p: Palette,
                         subtract_baseline: bool, label_fn: Callable[[Curve], str]) -> None:
    ax.clear()
    style_axes(ax, p)
    units = set()
    for n, (c, s, r) in enumerate(runs):
        if r.fit is None or not np.all(np.isfinite(r.conc_added)):
            continue
        color = p.series[n % len(p.series)]
        units.add(s.stock_unit)
        off = r.signal[0] if subtract_baseline and np.isfinite(r.signal[0]) else 0.0
        inc = r.included
        ax.plot(r.conc_added[inc], r.signal[inc] - off, "o", color=color, ms=5.5)
        if (~inc).any():
            ax.plot(r.conc_added[~inc], r.signal[~inc] - off, "o", mfc="none", mec=color, ms=5.5)
        xs = np.array([min(r.conc_added[inc].min(), 0.0), r.conc_added.max()])
        ax.plot(xs, r.fit.slope * xs + r.fit.intercept - off, color=color, lw=1.3,
                label=f"{label_fn(c)}: {g(r.fit.slope, 3)} µA/{s.stock_unit}")
    ax.set_xlabel(f"Added concentration ({next(iter(units))})" if len(units) == 1
                  else "Added concentration (units per run)")
    ax.set_ylabel("Signal − baseline (µA)" if subtract_baseline else "Signal (µA)")
    ax.set_title("Calibrations", loc="left", color=p.text)
    if ax.get_legend_handles_labels()[0]:
        ax.legend(loc="upper left", fontsize=8)
    else:
        _note(ax, "Enter stock, $V_0$ and V add\nin the CA tab to calibrate", p)


# --------------------------------------------------------------------------- off-screen rendering

def render_png(draw: Callable[[Figure, Palette], None], figsize: tuple[float, float] = (8.0, 3.8),
               dpi: Optional[int] = None, palette: Optional[Palette] = None) -> bytes:
    """Draw with ``draw(fig, palette)`` on a fresh off-screen figure and return PNG bytes.

    Without arguments the figure is light, uses the user's plot style and palette, and the export resolution
    from the preferences."""
    from .prefs import prefs
    palette = palette or report_palette()
    dpi = int(dpi or prefs.get("export_dpi"))
    with matplotlib.rc_context(mpl_rc(palette)):
        fig = Figure(figsize=figsize, layout="constrained")
        FigureCanvasAgg(fig)
        draw(fig, palette)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi)
    return buf.getvalue()


def style_preview(fig, p: Palette) -> None:
    """A small sample figure (a voltammetric peak with its fit, plus a second series) for the Settings window."""
    fig.clear()
    ax = fig.add_subplot(111)
    style_axes(ax, p)
    e = np.linspace(-0.2, 0.6, 200)
    base = 2.0 * e + 0.5
    for k, h in enumerate((8.0, 4.0, 1.6)):
        ax.plot(e, base + h * np.exp(-0.5 * ((e - 0.2) / 0.05) ** 2), color=p.series[k % len(p.series)], label=f"{(10, 5, 2)[k]} µM")
    ax.plot(e, base, color=p.extrap, ls="--", lw=1.1, label="baseline")
    ax.plot([0.2], [base[100] + 8.0], "v", color=p.cx, ms=7, label="peak")
    ax.set_xlabel("Potential (V)")
    ax.set_ylabel("Current (µA)")
    ax.set_title("Sample: peak height vs concentration", loc="left")
    ax.legend(loc="upper left", ncols=2)


def png_dpi(png: bytes) -> float:
    """Resolution stored in a PNG (its pHYs chunk), 96 if it has none."""
    pos = 8
    while pos + 8 <= len(png):
        n = int.from_bytes(png[pos:pos + 4], "big")
        if png[pos + 4:pos + 8] == b"pHYs" and n >= 9:
            return int.from_bytes(png[pos + 8:pos + 12], "big") * 0.0254
        pos += 12 + n
    return 96.0


def png_size(png: bytes) -> tuple[int, int]:
    """(width, height) in pixels, read from the PNG header (no image library needed)."""
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")


# --------------------------------------------------------------------------- sensor study

def _group_colors(groups: Sequence[str], p: Palette) -> dict[str, str]:
    names = sorted({g_ or "" for g_ in groups})
    return {n: p.series[i % len(p.series)] for i, n in enumerate(names)}


def study_calibration(fig, cs, p: Palette, logx: bool = False) -> None:
    """Calibration points (by electrode), the fitted model, the linear range and a residual panel."""
    from . import calibration as cal
    fig.clear()
    if cs is None or not len(cs.x):
        ax = fig.add_subplot(111)
        _note(ax, "Add responses and give them concentrations\n(Responses table) to draw a calibration.", p)
        ax.set_axis_off()
        return
    ok = cs.ok
    if ok:
        gs = fig.add_gridspec(2, 1, height_ratios=[4, 1.1])
        ax, axr = fig.add_subplot(gs[0]), fig.add_subplot(gs[1], sharex=None)
    else:
        ax, axr = fig.add_subplot(111), None
    colors = _group_colors(cs.groups, p)
    for g_ in sorted(set(cs.groups)):
        m = np.array([gg == g_ for gg in cs.groups])
        ax.plot(cs.x[m], cs.y[m], "o", color=colors[g_ or ""], ms=6.5, mec=p.plot_bg, mew=0.8, zorder=4,
                label=(g_ or "standards") if len(colors) > 1 else None)
    levels = cal.replicate_summary(cs.x, cs.y)
    if any(n > 1 for *_, n in levels):                      # replicates: mean ± SD
        lx = np.array([a for a, *_ in levels]); lm = np.array([b for _, b, *_ in levels])
        ls = np.array([c if c == c else 0 for _, _, c, _ in levels])
        ax.errorbar(lx, lm, yerr=ls, fmt="_", color=p.fit, ecolor=p.fit, capsize=3, ms=11, lw=1.2, zorder=5)
    if cs.blanks.size:
        ax.axhspan(cs.blanks.mean() - 3 * (cs.blanks.std(ddof=1) if cs.blanks.size > 1 else 0),
                   cs.blanks.mean() + 3 * (cs.blanks.std(ddof=1) if cs.blanks.size > 1 else 0), color=p.extrap, alpha=0.15, lw=0)
        ax.axhline(cs.blanks.mean(), color=p.extrap, lw=1.0, ls=":")
    if ok:
        lo, hi = float(np.min(cs.x)), float(np.max(cs.x))
        if logx:
            xs = np.logspace(np.log10(max(lo, 1e-12)), np.log10(hi), 200)
        else:
            xs = np.linspace(min(lo, 0.0) if cs.model == cal.LINEAR else lo, hi, 200)
        ax.plot(xs, cs.fit.predict(xs), color=p.fit, lw=1.7, zorder=3)
        if cs.fom and cs.fom.n_range and cs.model == cal.LINEAR:
            ax.axvspan(cs.fom.range_low, cs.fom.range_high, color=p.window, alpha=0.14 if p.name == "light" else 0.2, lw=0, zorder=0)
        text = [f"{cal.MODEL_LABELS[cs.model]}   R² = {cs.fit.r2:.4f}"]
        if math.isfinite(cs.sensitivity):
            text.append(f"S = {cs.sensitivity:.4g} {cs.sensitivity_unit}")
        if math.isfinite(cs.lod):
            text.append(f"LOD = {cs.lod:.3g} {cs.conc_unit}   LOQ = {cs.loq:.3g} {cs.conc_unit}")
        ax.text(0.03, 0.97, "\n".join(text), transform=ax.transAxes, ha="left", va="top", fontsize=8.8, color=p.text)
        axr.axhline(0, color=p.border_strong, lw=0.9)
        axr.plot(cs.fit.x, cs.fit.residuals, "o", color=p.trace, ms=4)
        axr.set_ylabel("Resid.")
        axr.set_xlabel(f"Concentration ({cs.conc_unit})")
        if logx:
            axr.set_xscale("log")
        ax.tick_params(labelbottom=False)
        axr.set_xlim(ax.get_xlim()) if not logx else None
    else:
        ax.set_xlabel(f"Concentration ({cs.conc_unit})")
    if logx:
        ax.set_xscale("log")
    ax.set_ylabel(f"{cs.options.quantity} ({cs.y_unit})")
    ax.set_title(f"Calibration · {cs.options.condition}", loc="left", color=p.text)
    if len(colors) > 1:
        ax.legend(loc="lower right", fontsize=8)


def study_reproducibility(fig, rs, p: Palette) -> None:
    fig.clear()
    ax = fig.add_subplot(1, 2, 1)
    if not rs.rows:
        _note(ax, "No standards with a concentration.", p)
        ax.set_axis_off()
        return
    xs = np.arange(len(rs.rows))
    colors = _group_colors([g_ for row in rs.rows for g_ in row.per_group], p)
    ax.bar(xs, [r.stats.mean for r in rs.rows], yerr=[r.stats.sd if r.stats.sd == r.stats.sd else 0 for r in rs.rows],
           color=p.accent_soft, edgecolor=p.border_strong, capsize=4, width=0.6)
    for k, row in enumerate(rs.rows):
        for j, (g_, v) in enumerate(sorted(row.per_group.items())):
            ax.plot(k + (j - (len(row.per_group) - 1) / 2) * 0.08, v, "o", color=colors[g_], ms=5, mec=p.plot_bg,
                    label=g_ if k == 0 else None, zorder=4)
        if row.stats.rsd_pct == row.stats.rsd_pct:
            ax.text(k, row.stats.mean + (row.stats.sd if row.stats.sd == row.stats.sd else 0), f" {row.stats.rsd_pct:.1f}%",
                    ha="center", va="bottom", fontsize=8, color=p.muted)
    ax.set_xticks(xs, [f"{r.conc:.4g}" for r in rs.rows])
    ax.set_xlabel(f"Concentration ({rs.conc_unit})")
    ax.set_ylabel(f"{rs.quantity} ({rs.y_unit})")
    ax.set_title("Between electrodes (mean ± SD, RSD %)", loc="left", color=p.text)
    if len(colors) > 1:
        ax.legend(loc="best", fontsize=7.5, ncols=2)
    ax2 = fig.add_subplot(1, 2, 2)
    if rs.sensitivity:
        names = sorted(rs.sensitivity)
        vals = [rs.sensitivity[n] for n in names]
        ax2.bar(range(len(names)), vals, color=[colors.get(n, p.trace) for n in names], width=0.6)
        if rs.sensitivity_stats and rs.sensitivity_stats.n > 1:
            m = rs.sensitivity_stats
            ax2.axhline(m.mean, color=p.fit, lw=1.2)
            ax2.axhspan(m.mean - m.sd, m.mean + m.sd, color=p.extrap, alpha=0.18, lw=0)
        ax2.set_xticks(range(len(names)), names, rotation=20 if len(names) > 4 else 0)
        ax2.set_ylabel(f"Sensitivity ({rs.y_unit}/{rs.conc_unit})")
        ax2.set_title("Sensitivity per electrode" + (f" · mean {rs.sensitivity_stats.mean:.4g}, RSD {rs.sensitivity_stats.rsd_pct:.1f} %"
                                                     if rs.sensitivity_stats and rs.sensitivity_stats.n > 1 else ""), loc="left", color=p.text, fontsize=10)
    else:
        _note(ax2, "Per-electrode sensitivity needs ≥ 3\nconcentrations on each electrode.", p)
        ax2.set_axis_off()


def study_stability(fig, st, p: Palette) -> None:
    fig.clear()
    ax = fig.add_subplot(111)
    r = st.result
    if r is None:
        _note(ax, "Fill the Time column to draw a stability plot.", p)
        ax.set_axis_off()
        return
    ax.plot(r.t, r.retention_pct, "o-", color=p.trace, ms=6, lw=1.3, zorder=4)
    ax.axhline(100, color=p.border_strong, lw=0.9)
    ax.axhline(90, color=p.step, lw=1.0, ls="--")
    ax.text(r.t.max(), 90, " 90 %", color=p.step, va="bottom", ha="right", fontsize=8)
    if math.isfinite(r.tau):
        tt = np.linspace(r.t.min(), r.t.max(), 200)
        ax.plot(tt, (r.y_inf + (r.y[0] - r.y_inf) * np.exp(-(tt - r.t[0]) / r.tau)) / r.y[0] * 100.0, color=p.cx, lw=1.4)
    txt = [f"retention at last point: {r.final_retention_pct:.1f} %"]
    if math.isfinite(r.drift_pct_per_unit):
        txt.append(f"drift: {r.drift_pct_per_unit:.3g} % per time unit")
    if math.isfinite(r.half_life):
        txt.append(f"half-life: {r.half_life:.3g}")
    if math.isfinite(r.t_90):
        txt.append(f"time to 90 %: {r.t_90:.3g}")
    ax.text(0.03, 0.05, "\n".join(txt), transform=ax.transAxes, ha="left", va="bottom", fontsize=8.8, color=p.text)
    ax.set_xlabel("Time")
    ax.set_ylabel("Response retained (%)")
    ax.set_title(f"Stability · {st.quantity}", loc="left", color=p.text)


def study_selectivity(fig, ss, p: Palette) -> None:
    fig.clear()
    ax = fig.add_subplot(111)
    if not ss.rows:
        _note(ax, "Set the Condition column (e.g. “ascorbate”) for interferent\nresponses to draw a selectivity chart.", p)
        ax.set_axis_off()
        return
    names = [r.interferent for r in ss.rows]
    vals = [r.change_pct if r.change_pct == r.change_pct else 0 for r in ss.rows]
    cols = [p.cx if abs(v) > 10 else p.window for v in vals]
    ax.barh(range(len(names)), vals, color=cols, height=0.55)
    ax.axvline(0, color=p.border_strong, lw=0.9)
    ax.axvspan(-10, 10, color=p.window, alpha=0.10, lw=0)
    for k, r in enumerate(ss.rows):
        lab = f"{vals[k]:+.1f} %" + (f"   K = {r.k_sel:.3g}" if r.k_sel == r.k_sel else "")
        ax.text(vals[k], k, "  " + lab, va="center", ha="left" if vals[k] >= 0 else "right", fontsize=8.5, color=p.text)
    ax.set_yticks(range(len(names)), names)
    ax.invert_yaxis()
    ax.set_xlabel(f"Response vs {ss.analyte} alone ({ss.analyte_conc:.4g} {ss.conc_unit}), %")
    ax.set_title("Selectivity (within ±10 % shaded)", loc="left", color=p.text)


def study_recovery(fig, cs, p: Palette) -> None:
    fig.clear()
    ax = fig.add_subplot(111)
    rows = [s for s in cs.samples if s.recovery_pct == s.recovery_pct] if cs is not None else []
    if not rows:
        _note(ax, "Samples with an expected concentration give a recovery.", p)
        ax.set_axis_off()
        return
    xs = np.arange(len(rows))
    vals = [s.recovery_pct for s in rows]
    ax.bar(xs, vals, color=[p.window if 90 <= v <= 110 else p.step for v in vals], width=0.6)
    ax.axhline(100, color=p.fit, lw=1.2)
    ax.axhspan(90, 110, color=p.window, alpha=0.10, lw=0)
    for k, v in enumerate(vals):
        ax.text(k, v, f"{v:.1f}", ha="center", va="bottom", fontsize=8.5, color=p.text)
    ax.set_xticks(xs, [s.label for s in rows], rotation=20 if len(rows) > 4 else 0)
    ax.set_ylabel("Recovery (%)")
    ax.set_title("Spike recovery (90–110 % shaded)", loc="left", color=p.text)


# --------------------------------------------------------------------------- electrode characterisation

def electrode_scan_rate(fig, a, p: Palette) -> None:
    """ip vs √ν, log–log exponent, Ep vs ln ν and ΔEp vs ν for a scan-rate series."""
    fig.clear()
    ax = [fig.add_subplot(2, 2, k + 1) for k in range(4)]
    if a is None or len(a.nu) < 2:
        for x in ax[1:]:
            x.set_axis_off()
        _note(ax[0], "Tick at least 3 runs recorded at different\nscan rates (and give each its scan rate).", p)
        return
    sq = np.sqrt(a.nu)
    for ip, fit, col, name in ((a.ip_a, a.fit_a, p.trace, "anodic"), (a.ip_c, a.fit_c, p.cx, "cathodic")):
        ax[0].plot(sq, ip, "o", color=col, ms=5.5, label=name)
        if fit is not None and fit.ok:
            xs = np.linspace(0, sq.max(), 50)
            ax[0].plot(xs, fit.predict(xs), color=col, lw=1.3)
    ax[0].axhline(0, color=p.border_strong, lw=0.9)
    ax[0].set_xlabel("√ν  (V/s)½")
    ax[0].set_ylabel("ip (µA)")
    ax[0].set_title("Randles–Sevcik", loc="left", color=p.text)
    ax[0].legend(loc="best")
    for ip, col, name in ((a.ip_a, p.trace, "anodic"), (a.ip_c, p.cx, "cathodic")):
        ok = np.isfinite(ip) & (ip != 0) & (a.nu > 0)
        ax[1].plot(np.log10(a.nu[ok]), np.log10(np.abs(ip[ok])), "o", color=col, ms=5.5)
    ex = np.nanmean([a.loglog_a, a.loglog_c])
    ax[1].text(0.04, 0.95, f"slope ≈ {ex:.2f}\n{a.regime}" if np.isfinite(ex) else "", transform=ax[1].transAxes, va="top", fontsize=8.8, color=p.text)
    ax[1].set_xlabel("log ν")
    ax[1].set_ylabel("log |ip|")
    ax[1].set_title("0.5 = diffusion · 1 = adsorption", loc="left", color=p.text)
    ax[2].plot(np.log(a.nu), a.ep_a, "o", color=p.trace, ms=5.5)
    ax[2].plot(np.log(a.nu), a.ep_c, "o", color=p.cx, ms=5.5)
    lv = a.laviron
    if lv is not None and np.isfinite(lv.alpha):
        ax[2].text(0.04, 0.95, f"α = {lv.alpha:.2f}   n = {lv.n:.2f}\nks = {lv.ks:.3g} s⁻¹", transform=ax[2].transAxes, va="top", fontsize=8.8, color=p.text)
    ax[2].set_xlabel("ln ν")
    ax[2].set_ylabel("Ep (V)")
    ax[2].set_title("Laviron", loc="left", color=p.text)
    ax[3].plot(a.nu, a.delta_ep * 1000, "o-", color=p.step, ms=5.5, lw=1.2)
    ax[3].set_xlabel("ν (V/s)")
    ax[3].set_ylabel("ΔEp (mV)")
    ax[3].set_title("Peak separation", loc="left", color=p.text)


def electrode_cdl(fig, nu, di, out: dict, p: Palette) -> None:
    fig.clear()
    ax = fig.add_subplot(111)
    nu, di = np.asarray(nu, float), np.asarray(di, float)
    if len(nu) < 2:
        _note(ax, "Tick at least 3 non-faradaic CV runs\nat different scan rates.", p)
        ax.set_axis_off()
        return
    ax.plot(nu, di, "o", color=p.trace, ms=6.5)
    if np.isfinite(out.get("cdl_uF", np.nan)):
        xs = np.linspace(0, nu.max(), 50)
        f = np.polyfit(nu, di, 1)
        ax.plot(xs, np.polyval(f, xs), color=p.fit, lw=1.5)
        ax.text(0.04, 0.95, f"Cdl = {out['cdl_uF']:.4g} µF\nR² = {out['r2']:.4f}", transform=ax.transAxes, va="top", fontsize=9, color=p.text)
    ax.axhline(0, color=p.border_strong, lw=0.9)
    ax.set_xlabel("Scan rate ν (V/s)")
    ax.set_ylabel("(ia − ic)/2 at E_mid  (µA)")
    ax.set_title("Double-layer capacitance", loc="left", color=p.text)
