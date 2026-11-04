"""Analyzers: one uniform way to analyse any measurement (no Qt).

An :class:`Analyzer` declares its settings (:class:`Field`), runs an engine on a :class:`Curve`, draws its own
figure, and lists the numbers worth sending to the sensor study (:class:`ResponseSpec`).  The generic Analysis tab,
the report builder and the tests all use this one interface, so adding a technique means adding one class here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from . import electrode, peaks, techniques, transients
from . import eis as eis_engine
from .ca_analysis import analyze_ca, detect_step_events
from .io_palmsens import parse_stock_from_title
from .labels import curve_run_label
from .prefs import prefs
from .models import CAAnalysisSettings, Curve, Response
from .palette import Palette

NAN = float("nan")


@dataclass
class Field:
    key: str
    label: str
    kind: str                          # float | int | bool | choice
    default: Any
    lo: float = 0.0
    hi: float = 1e9
    step: float = 0.1
    decimals: int = 3
    suffix: str = ""
    tip: str = ""
    choices: tuple[tuple[str, Any], ...] = ()
    advanced: bool = False


@dataclass
class Metric:
    label: str
    value: str
    sub: str = ""
    tone: str = ""                     # "", success, warning, danger


@dataclass
class ResponseSpec:
    quantity: str
    unit: str
    value: float
    sd: Optional[float] = None
    suffix: str = ""                   # distinguishes several responses from one measurement (e.g. "segment 2")


@dataclass
class AnalysisResult:
    ok: bool = False
    metrics: list[Metric] = field(default_factory=list)
    rows: list[tuple[str, str, str]] = field(default_factory=list)          # (name, value, unit)
    table: Optional[tuple[list[str], list[list[str]]]] = None
    responses: list[ResponseSpec] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data: Any = None                   # engine objects used by ``draw``


def g(x: Any, digits: int = 4) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    return f"{x:.{digits}g}" if isinstance(x, (int, float, np.floating)) else str(x)


def guess_conc(curve: Curve) -> Optional[tuple[float, str]]:
    """A concentration written in the run or curve title ('10 µM', '505.1mM'), if any."""
    for text in (curve.meta.get("run_title", ""), curve.title):
        found = parse_stock_from_title(text or "")
        if found:
            return found
    return None


class Analyzer:
    key: str = ""
    title: str = ""
    kinds: tuple[str, ...] = ()
    fields: tuple[Field, ...] = ()
    figsize: tuple[float, float] = (7.0, 4.2)
    description: str = ""

    def defaults(self) -> dict[str, Any]:
        return {f.key: f.default for f in self.fields}

    def params_for(self, stored: Optional[dict[str, Any]]) -> dict[str, Any]:
        out = self.defaults()
        out.update({k: v for k, v in (stored or {}).items() if k in out})
        return out

    def applies_to(self, kind: str) -> bool:
        return kind in self.kinds

    def run(self, curve: Curve, params: dict[str, Any]) -> AnalysisResult:          # pragma: no cover (abstract)
        raise NotImplementedError

    def draw(self, fig, curve: Curve, params: dict[str, Any], result: AnalysisResult, p: Palette) -> None:
        raise NotImplementedError


def _note(fig, text: str, p: Palette) -> None:
    fig.clear()
    ax = fig.add_subplot(111)
    ax.set_axis_off()
    ax.text(0.5, 0.5, text, ha="center", va="center", transform=ax.transAxes, color=p.muted, fontsize=10.5, wrap=True)


# =============================================================================== peaks

class PeakAnalyzer(Analyzer):
    key = "peak"
    title = "Peak analysis"
    kinds = (techniques.DPV, techniques.SWV, techniques.NPV, techniques.LSV, techniques.ACV, techniques.CV, techniques.OTHER)
    description = "Baseline-corrected peak height, position, width and area (DPV, SWV, LSV, a single CV scan, …)."
    fields = (
        Field("use_window", "Restrict to a window", "bool", False, tip="Search only between the two potentials below."),
        Field("win_lo", "Window from", "float", -0.1, -20, 20, 0.01, 3, "V"),
        Field("win_hi", "Window to", "float", 0.5, -20, 20, 0.01, 3, "V"),
        Field("baseline", "Baseline", "choice", "linear", choices=(("Linear (through the window ends)", "linear"),
                                                                    ("Polynomial (fitted to the ends)", "poly"),
                                                                    ("None (raw current)", "none"))),
        Field("poly_order", "Polynomial order", "int", 2, 1, 5, 1, 0, advanced=True),
        Field("anchor_pct", "Baseline anchors", "float", 15.0, 3, 45, 1, 0, "% each end", "Share of the window at each end used to draw the baseline.", advanced=True),
        Field("polarity", "Peak direction", "choice", "auto", choices=(("Automatic", "auto"), ("Anodic (up)", "up"), ("Cathodic (down)", "down"))),
        Field("smooth", "Smoothing", "int", 0, 0, 101, 2, 0, "points", "Savitzky–Golay window; 0 = off.", advanced=True),
    )

    def _params(self, params: dict[str, Any]) -> peaks.PeakParams:
        return peaks.PeakParams(window=(params["win_lo"], params["win_hi"]) if params["use_window"] else None,
                                baseline=params["baseline"], poly_order=int(params["poly_order"]),
                                anchor_frac=params["anchor_pct"] / 100.0, polarity=params["polarity"], smooth=int(params["smooth"]))

    def run(self, curve: Curve, params: dict[str, Any]) -> AnalysisResult:
        r = peaks.analyze_peak(curve.x, curve.y, self._params(params))
        out = AnalysisResult(ok=r.found, data=r, warnings=list(r.warnings))
        if not r.found:
            return out
        u = curve.y_unit or "µA"
        out.metrics = [Metric("Peak height", g(r.height), u), Metric("Peak potential", g(r.e_peak), "V"),
                       Metric("Width (FWHM)", g(r.fwhm * 1000 if math.isfinite(r.fwhm) else NAN, 3), "mV"),
                       Metric("Area", g(r.area), f"{u}·V")]
        out.rows = [("Direction", r.polarity, ""), ("Peak height (baseline-corrected)", g(r.height), u),
                    ("Peak current (raw)", g(r.height_raw), u), ("Peak potential", g(r.e_peak), "V"),
                    ("FWHM", g(r.fwhm * 1000 if math.isfinite(r.fwhm) else NAN), "mV"), ("Area", g(r.area), f"{u}·V")]
        out.responses = [ResponseSpec("Peak height", u, r.height), ResponseSpec("Peak potential", "V", r.e_peak)]
        if curve.kind == techniques.LSV:
            sf = peaks.sweep_features(curve.x, curve.y)
            out.rows += [("Limiting current (sigmoidal sweep)", g(sf.limiting), u), ("Half-wave potential E½", g(sf.half_wave), "V"),
                         ("Onset potential", g(sf.onset), "V")]
            out.warnings += sf.warnings
            if math.isfinite(sf.limiting):
                out.responses.append(ResponseSpec("Limiting current", u, sf.limiting))
        return out

    def draw(self, fig, curve, params, result, p) -> None:
        r: peaks.PeakResult = result.data
        fig.clear()
        if r is None or not r.found:
            _note(fig, "No peak found. Check the window.", p)
            return
        gs = fig.add_gridspec(2, 1, height_ratios=[3, 2])
        ax, axc = fig.add_subplot(gs[0]), fig.add_subplot(gs[1], sharex=None)
        ax.plot(curve.x, curve.y, color=p.trace, lw=1.2, label="measured")
        ax.plot(r.e, r.baseline, color=p.extrap, lw=1.3, ls="--", label="baseline")
        if params["use_window"]:
            ax.axvspan(params["win_lo"], params["win_hi"], color=p.step, alpha=0.09 if p.name == "light" else 0.14, lw=0)
        k = int(np.argmin(np.abs(r.e - r.e_peak)))
        ax.plot([r.e_peak], [r.i[k]], "v" if r.height < 0 else "^", color=p.cx, ms=8, mec=p.text, zorder=5)
        ax.vlines(r.e_peak, r.baseline[k], r.i[k], color=p.cx, lw=1.4)
        ax.set_ylabel(f"Current ({curve.y_unit})")
        ax.set_title(f"{curve.kind} · peak at {r.e_peak:.3f} V, height {r.height:.4g} {curve.y_unit}", loc="left", color=p.text)
        ax.legend(loc="best")
        ax.tick_params(labelbottom=False)
        axc.plot(r.e, r.corrected, color=p.trace, lw=1.2)
        axc.fill_between(r.e, 0, r.corrected, color=p.trace, alpha=0.18)
        axc.axhline(0, color=p.border_strong, lw=0.9)
        axc.axvline(r.e_peak, color=p.cx, lw=1.0, ls=":")
        axc.set_xlabel("Potential (V)")
        axc.set_ylabel("Corrected")
        axc.set_xlim(ax.get_xlim())


# =============================================================================== impedance

class EISAnalyzer(Analyzer):
    key = "eis_fit"
    title = "Equivalent-circuit fit"
    kinds = (techniques.EIS,)
    figsize = (8.5, 4.2)
    description = "Fit Rs, Rct, CPE (and Warburg) to a spectrum; effective capacitance and, optionally, k⁰."
    fields = (
        Field("circuit", "Circuit", "choice", "randles", choices=tuple((v[0], k) for k, v in eis_engine.CIRCUITS.items())),
        Field("fmin", "Lowest frequency used", "float", 0.0, 0, 1e7, 0.1, 3, "Hz", "0 = use all."),
        Field("fmax", "Highest frequency used", "float", 0.0, 0, 1e9, 100, 1, "Hz", "0 = use all."),
        Field("area_cm2", "Electrode area", "float", 0.0, 0, 1e3, 0.01, 5, "cm²", "For k⁰ (with the concentration below)."),
        Field("conc_mM", "Redox probe conc.", "float", 0.0, 0, 1e4, 0.5, 3, "mM", "e.g. 5 mM [Fe(CN)6]3−/4−."),
        Field("n_e", "Electrons (n)", "int", 1, 1, 6, 1, 0, advanced=True),
        Field("temp_c", "Temperature", "float", 25.0, -10, 90, 1, 1, "°C", advanced=True),
    )

    def run(self, curve: Curve, params: dict[str, Any]) -> AnalysisResult:
        out = AnalysisResult()
        if not {"freq", "zre", "zim"} <= curve.extra.keys():
            out.warnings.append("This measurement has no frequency data (only a Nyquist x–y). Use File ▸ Import table to "
                                "load frequency, Z′ and Z″.")
            return out
        fit = eis_engine.fit_circuit(curve.extra["freq"], curve.extra["zre"], curve.extra["zim"], params["circuit"],
                                     fmin=params["fmin"] or 0.0, fmax=params["fmax"] or np.inf)
        out.data, out.warnings = fit, list(fit.warnings)
        out.ok = bool(np.isfinite(fit.params.get("Rct", NAN)))
        if not out.ok:
            return out
        P, S = fit.params, fit.se
        tone = "success" if fit.rmse_rel < 0.02 else ("warning" if fit.rmse_rel < 0.05 else "danger")
        k0 = eis_engine.k0_from_rct(P["Rct"], params["area_cm2"], params["conc_mM"], int(params["n_e"]), params["temp_c"])
        out.metrics = [Metric("Rct", g(P["Rct"]), f"Ω ± {g(S.get('Rct'), 2)}"), Metric("Rs", g(P["Rs"]), f"Ω ± {g(S.get('Rs'), 2)}"),
                       Metric("C eff", g(fit.c_eff * 1e6, 3), "µF"), Metric("Fit quality", f"{fit.rmse_rel * 100:.2g} %", "RMS relative error", tone)]
        for n_, v in P.items():
            out.rows.append((n_, f"{g(v)} ± {g(S.get(n_), 2)}", {"Rs": "Ω", "Rct": "Ω", "C": "F", "Q": "S·sⁿ", "n": "", "sigma": "Ω·s⁻½"}.get(n_, "")))
        out.rows += [("C effective (Brug)", g(fit.c_eff * 1e6), "µF"), ("RMS relative error", f"{fit.rmse_rel * 100:.3g}", "%"),
                     ("Frequencies used", str(len(fit.f)), "points")]
        if math.isfinite(k0):
            out.rows.append(("k⁰ (from Rct)", g(k0), "cm/s"))
            out.metrics[1] = Metric("k⁰", g(k0, 3), "cm/s")
        out.responses = [ResponseSpec("Rct", "Ω", P["Rct"], S.get("Rct")), ResponseSpec("Rs", "Ω", P["Rs"], S.get("Rs")),
                         ResponseSpec("C effective", "µF", fit.c_eff * 1e6)]
        if math.isfinite(k0):
            out.responses.append(ResponseSpec("k0", "cm/s", k0))
        return out

    def draw(self, fig, curve, params, result, p) -> None:
        fit: Optional[eis_engine.EISFit] = result.data
        fig.clear()
        if fit is None or "freq" not in curve.extra:
            _note(fig, "No impedance spectrum to draw.", p)
            return
        ax = fig.add_subplot(1, 2, 1)
        zall = curve.extra["zre"] + 1j * curve.extra["zim"]
        ax.plot(zall.real, -zall.imag, "o", color=p.border_strong, ms=3.5, label="all data")
        ax.plot(fit.z.real, -fit.z.imag, "o", color=p.trace, ms=4.5, label="used in fit")
        if np.isfinite(fit.z_fit).all():
            ax.plot(fit.z_fit.real, -fit.z_fit.imag, "-", color=p.cx, lw=1.7, label="fit")
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlabel("Z′ (Ω)")
        ax.set_ylabel("−Z″ (Ω)")
        ax.set_title("Nyquist", loc="left", color=p.text)
        ax.legend(loc="best")
        ax2 = fig.add_subplot(1, 2, 2)
        mag, ph = eis_engine.bode(curve.extra["freq"], zall)
        ax2.loglog(curve.extra["freq"], mag, "o", color=p.trace, ms=3.5, label="|Z|")
        if np.isfinite(fit.z_fit).all():
            ax2.loglog(fit.f, np.abs(fit.z_fit), "-", color=p.cx, lw=1.5)
        ax2.set_xlabel("Frequency (Hz)")
        ax2.set_ylabel("|Z| (Ω)", color=p.trace)
        ax2.set_title("Bode", loc="left", color=p.text)
        ax3 = ax2.twinx()
        ax3.spines["right"].set_visible(True)
        ax3.semilogx(curve.extra["freq"], ph, "s", color=p.step, ms=3, alpha=0.8)
        if np.isfinite(fit.z_fit).all():
            ax3.semilogx(fit.f, eis_engine.bode(fit.f, fit.z_fit)[1], "-", color=p.step, lw=1.2)
        ax3.set_ylabel("Phase (°)", color=p.step)
        ax3.grid(False)


# =============================================================================== time series

class SteadyAnalyzer(Analyzer):
    key = "steady"
    title = "Steady state, noise and drift"
    kinds = (techniques.CA, techniques.OCP, techniques.CP, techniques.CC, techniques.OTHER)
    description = "Mean level of a stretch of a trace, its noise, drift and signal-to-noise."
    fields = (
        Field("window", "Averaging window", "float", 10.0, 0.1, 1e6, 1.0, 1, "s", "Length of the stretch analysed."),
        Field("t_end", "Window ends at", "float", 0.0, 0, 1e6, 1.0, 1, "s", "0 = the end of the trace."),
        Field("drop_last", "Drop last N points", "int", 0, 0, 100, 1, 0, advanced=True),
    )

    def run(self, curve: Curve, params: dict[str, Any]) -> AnalysisResult:
        out = AnalysisResult()
        n = len(curve.x) - int(params["drop_last"])
        t, y = curve.x[:n], curve.y[:n]
        t1 = params["t_end"] or float(t[-1])
        t0 = t1 - params["window"]
        s = transients.steady_state(t, y, t0, t1)
        out.data = (t, y, t0, t1, s)
        if s.n < 3:
            out.warnings.append("The window holds fewer than 3 points.")
            return out
        out.ok = True
        u = curve.y_unit
        out.metrics = [Metric("Level", g(s.level), u), Metric("Noise (SD)", g(s.sd), u), Metric("Drift", g(s.drift_rel_pct_per_min, 3), "% / min"),
                       Metric("S/N", g(s.snr, 3), "")]
        out.rows = [("Level (mean)", g(s.level), u), ("Noise SD about the trend", g(s.sd), u), ("Drift", g(s.drift * 60, 3), f"{u}/min"),
                    ("Drift, relative", g(s.drift_rel_pct_per_min, 3), "% / min"), ("Signal / noise", g(s.snr, 3), ""),
                    ("Window", f"{t0:.1f} – {t1:.1f}", "s"), ("Points", str(s.n), "")]
        out.responses = [ResponseSpec("Steady-state level", u, s.level, s.sd), ResponseSpec("Drift", f"{u}/s", s.drift)]
        if s.drift_rel_pct_per_min and abs(s.drift_rel_pct_per_min) > 1.0:
            out.warnings.append("The signal is still drifting by more than 1 % per minute: the level may not be settled.")
        return out

    def draw(self, fig, curve, params, result, p) -> None:
        fig.clear()
        if result.data is None:
            _note(fig, "Nothing to draw.", p)
            return
        t, y, t0, t1, s = result.data
        ax = fig.add_subplot(111)
        ax.plot(t, y, color=p.trace, lw=1.1)
        ax.axvspan(t0, t1, color=p.window, alpha=0.18 if p.name == "light" else 0.22, lw=0)
        if math.isfinite(s.level):
            ax.hlines(s.level, t0, t1, color=p.plateau, lw=2.4, zorder=5)
            ax.plot([t0, t1], [s.level + s.drift * (t0 - (t0 + t1) / 2), s.level + s.drift * (t1 - (t0 + t1) / 2)], color=p.cx, lw=1.2, ls="--")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel(f"{curve.y_unit}")
        ax.set_title(f"Level {g(s.level)} {curve.y_unit} · drift {g(s.drift_rel_pct_per_min, 3)} %/min", loc="left", color=p.text)


class StaircaseAnalyzer(Analyzer):
    key = "staircase"
    title = "Step responses (additions)"
    kinds = (techniques.CA, techniques.OCP, techniques.CP)
    description = ("Detects the steps of an additions experiment, reports the plateau after each one and its response "
                   "time; send the plateaus to the sensor study and assign concentrations there.")
    fields = (
        Field("k", "Step threshold", "float", 8.0, 2, 100, 1, 1, "× noise σ"),
        Field("min_sep", "Min. step separation", "float", 5.0, 0.5, 600, 1, 1, "s"),
        Field("settle", "Settle time", "float", 3.0, 0, 600, 0.5, 1, "s"),
        Field("window", "Averaging window", "float", 5.0, 0.2, 600, 0.5, 1, "s"),
        Field("sharp", "Step shape", "choice", "auto", tip="Sharp steps are told apart from smooth decays by how abruptly "
              "they happen; potentiometric and slow biosensor responses take seconds and need 'allow slow'.",
              choices=(("Automatic (by technique)", "auto"), ("Sharp steps only", "sharp"), ("Allow slow responses", "slow"))),
        Field("expected", "Expected steps", "int", 0, 0, 50, 1, 0, "", "0 = automatic.", advanced=True),
        Field("drop_last", "Drop last N points", "int", 1, 0, 100, 1, 0, advanced=True),
    )

    @staticmethod
    def _abruptness(curve: Curve, mode: str) -> float:
        if mode == "sharp":
            return 0.25
        if mode == "slow":
            return 0.0
        return 0.0 if curve.kind in (techniques.OCP, techniques.CP) else 0.25

    def run(self, curve: Curve, params: dict[str, Any]) -> AnalysisResult:
        out = AnalysisResult()
        n = len(curve.x) - int(params["drop_last"])
        t, y = curve.x[:n], curve.y[:n]
        steps, ignored = detect_step_events(t, y, k=params["k"], min_sep=params["min_sep"], expected=int(params["expected"]),
                                            abruptness=self._abruptness(curve, params["sharp"]))
        s = CAAnalysisSettings(drop_last=0, step_times=[float(v) for v in steps], ignored_events=[float(v) for v in ignored],
                               settle=params["settle"], window=params["window"])
        r = analyze_ca(t, y, s)
        out.data = (curve, s, r)
        out.warnings = [w for w in r.warnings if "Enter stock" not in w]
        if not len(steps):
            out.warnings.append("No steps were detected. Lower the threshold or check the trace.")
            return out
        out.ok = True
        u = curve.y_unit
        rt_rows, t90 = [], []
        bounds = [float(t[0])] + list(map(float, steps)) + [float(t[-1])]
        for k in range(1, len(bounds) - 1):
            seg_end = bounds[k + 1]
            # The detected time is the middle of the change; a slow response starts earlier. Walk back to where the
            # signal leaves the noise of the previous plateau so t90 is measured from the real onset.
            prev, sd_pre = float(r.plateau[k - 1]), max(float(r.plateau_sd[k - 1]), 1e-12)
            j = int(np.searchsorted(t, bounds[k]))
            while j > 0 and t[j] > bounds[k - 1] + params["settle"] and abs(y[j] - prev) > 3 * sd_pre:
                j -= 1
            onset = float(t[j])
            rt = transients.response_time(t, y, onset, pre=min(5.0, max(onset - bounds[k - 1], 0.2)),
                                          post_window=min(params["window"], seg_end - bounds[k]), t_end=seg_end - 0.3)
            t90.append(rt.t_high)
            rt_rows.append(rt)
        header = ["Segment", "t step (s)", f"Plateau ({u})", f"± sd ({u})", f"Δ vs baseline ({u})", "t90 (s)"]
        rows = [["0 (baseline)", "—", g(r.plateau[0]), g(r.plateau_sd[0]), "0", "—"]]
        for k in range(1, len(r.plateau)):
            rows.append([str(k), f"{steps[k - 1]:.1f}", g(r.plateau[k]), g(r.plateau_sd[k]), g(r.delta[k]), g(t90[k - 1], 3)])
        out.table = (header, rows)
        out.metrics = [Metric("Steps", str(len(steps)), ""), Metric("Total change", g(r.delta[-1]), u),
                       Metric("Median t90", g(float(np.nanmedian(t90)) if np.isfinite(t90).any() else NAN, 3), "s"),
                       Metric("Noise", g(float(np.nanmedian(r.plateau_sd)), 2), u)]
        out.rows = [("Steps detected", str(len(steps)), ""), ("Baseline", g(r.plateau[0]), u), ("Final plateau", g(r.plateau[-1]), u)]
        out.responses = [ResponseSpec("Plateau", u, float(r.plateau[k]), float(r.plateau_sd[k]), suffix=("baseline" if k == 0 else f"step {k}"))
                         for k in range(len(r.plateau))]
        out.responses += [ResponseSpec("Step change (Δ)", u, float(r.delta[k]), suffix=f"step {k}") for k in range(1, len(r.plateau))]
        out.responses += [ResponseSpec("t90", "s", float(t90[k - 1]), suffix=f"step {k}") for k in range(1, len(r.plateau))
                          if math.isfinite(t90[k - 1])]
        return out

    def draw(self, fig, curve, params, result, p) -> None:
        from . import plots
        fig.clear()
        if result.data is None:
            _note(fig, "Nothing to draw.", p)
            return
        c, s, r = result.data
        ax = fig.add_subplot(111)
        plots.ca_trace(ax, c, s, r, p, title=f"{curve.kind} · {len(r.step_times)} steps")


class CottrellAnalyzer(Analyzer):
    key = "cottrell"
    title = "Cottrell / Anson (diffusion)"
    kinds = (techniques.CA, techniques.CC)
    description = "Diffusion coefficient from a chronoamperometric step (Cottrell) or chronocoulometry (Anson); adsorbed charge."
    fields = (
        Field("t0", "Window from", "float", 0.5, 0, 1e6, 0.1, 2, "s", "After the double-layer charging transient."),
        Field("t1", "Window to", "float", 10.0, 0.1, 1e6, 1.0, 2, "s"),
        Field("n_e", "Electrons (n)", "int", 1, 1, 6, 1, 0),
        Field("area_cm2", "Electrode area", "float", 0.0, 0, 1e3, 0.01, 5, "cm²"),
        Field("conc_mM", "Concentration", "float", 0.0, 0, 1e4, 0.5, 3, "mM"),
        Field("q_dl", "Double-layer charge", "float", 0.0, 0, 1e6, 0.1, 3, "µC", "Subtracted from the Anson intercept for Γ.", advanced=True),
    )

    def run(self, curve: Curve, params: dict[str, Any]) -> AnalysisResult:
        out = AnalysisResult()
        anson = curve.kind == techniques.CC
        if anson:
            r = transients.anson(curve.x, curve.y, params["t0"], params["t1"], int(params["n_e"]), params["area_cm2"],
                                 params["conc_mM"], params["q_dl"])
            lin = (r.slope, r.intercept, r.r2)
        else:
            r = transients.cottrell(curve.x, curve.y, params["t0"], params["t1"], int(params["n_e"]), params["area_cm2"], params["conc_mM"])
            lin = (r.slope, r.intercept, r.r2)
        out.data = (anson, r)
        out.warnings = list(r.warnings)
        if not math.isfinite(r.slope):
            return out
        out.ok = True
        out.metrics = [Metric("Slope", g(r.slope), "µA·s½" if not anson else "µC·s⁻½"), Metric("R²", g(r.r2, 4), "linearity",
                       "success" if r.r2 >= 0.995 else ("warning" if r.r2 >= 0.98 else "danger")),
                       Metric("D", g(r.d_cm2s, 3), "cm²/s"), Metric("Γ" if anson else "Intercept", g(getattr(r, "gamma_mol_cm2", r.intercept), 3),
                                                                      "mol/cm²" if anson else "µA")]
        out.rows = [("Slope", g(r.slope), "µA·s½" if not anson else "µC·s⁻½"), ("Intercept", g(r.intercept), "µA" if not anson else "µC"),
                    ("R²", g(r.r2, 5), ""), ("D", g(r.d_cm2s), "cm²/s")]
        if anson:
            out.rows.append(("Adsorbed amount Γ", g(r.gamma_mol_cm2), "mol/cm²"))
        out.responses = [ResponseSpec("Cottrell slope" if not anson else "Anson slope", "µA·s½" if not anson else "µC·s⁻½", r.slope)]
        if math.isfinite(r.d_cm2s):
            out.responses.append(ResponseSpec("Diffusion coefficient D", "cm²/s", r.d_cm2s))
        return out

    def draw(self, fig, curve, params, result, p) -> None:
        fig.clear()
        if result.data is None:
            _note(fig, "Nothing to draw.", p)
            return
        anson, r = result.data
        ax = fig.add_subplot(1, 2, 1)
        ax.plot(curve.x, curve.y, color=p.trace, lw=1.1)
        ax.axvspan(params["t0"], params["t1"], color=p.window, alpha=0.18 if p.name == "light" else 0.22, lw=0)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel(curve.y_unit)
        ax.set_title("Transient", loc="left", color=p.text)
        ax2 = fig.add_subplot(1, 2, 2)
        m = (curve.x > 0) & (curve.x >= params["t0"]) & (curve.x <= params["t1"])
        if m.sum() and math.isfinite(r.slope):
            xx = np.sqrt(curve.x[m]) if anson else curve.x[m] ** -0.5
            yy = curve.y[m] if anson else np.abs(curve.y[m])
            ax2.plot(xx, yy, "o", color=p.trace, ms=3.5)
            xs = np.array([xx.min(), xx.max()])
            ax2.plot(xs, r.intercept + r.slope * xs, color=p.cx, lw=1.6)
        ax2.set_xlabel("t½ (s½)" if anson else "t⁻½ (s⁻½)")
        ax2.set_ylabel("Q (µC)" if anson else "|i| (µA)")
        ax2.set_title("Anson plot" if anson else "Cottrell plot", loc="left", color=p.text)


ANALYZERS: dict[str, Analyzer] = {a.key: a for a in (PeakAnalyzer(), EISAnalyzer(), StaircaseAnalyzer(), SteadyAnalyzer(), CottrellAnalyzer())}


def for_kind(kind: str) -> list[Analyzer]:
    """Analyzers that apply to a technique, most specific first (OTHER curves accept the generic ones)."""
    found = [a for a in ANALYZERS.values() if kind in a.kinds]
    return found or [a for a in ANALYZERS.values() if techniques.OTHER in a.kinds]


def default_analyzer(kind: str) -> Analyzer:
    return for_kind(kind)[0]


def upsert_responses(responses: list[Response], curve: Curve, analyzer_key: str, specs: list[ResponseSpec]) -> list[Response]:
    """Add an analysis's numbers to ``responses`` (in place), or update the rows it already put there.

    A row's id is ``uid|analyzer|quantity|suffix``, so analysing again refreshes the value while the concentration,
    group, time, role and note the user typed are kept.  A concentration written in the title ('10 µM') is the first guess.
    """
    guess = guess_conc(curve)
    label = curve_run_label(curve)
    out = []
    for sp in specs:
        rid = f"{curve.uid}|{analyzer_key}|{sp.quantity}|{sp.suffix}"
        row = next((r for r in responses if r.id == rid), None)
        if row is None:
            row = Response(id=rid, source_uid=curve.uid, analyzer=analyzer_key, conc=guess[0] if guess else None,
                           conc_unit=guess[1] if guess else prefs.get("conc_unit"))
            responses.append(row)
        row.source_label = f"{label} · {sp.suffix}" if sp.suffix else label
        row.technique, row.quantity, row.unit, row.value, row.sd = curve.kind, sp.quantity, sp.unit, float(sp.value), sp.sd
        out.append(row)
    return out
