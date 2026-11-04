"""The in-app guide (no Qt): what every analysis does, the formulas behind the numbers, shortcuts and limits.

Each topic is a little HTML (the subset Qt's text browser renders).  Links between topics use ``topic:<id>``.
Keeping it here, away from the widgets, lets tests check that every link resolves and every shortcut is real.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Topic:
    id: str
    group: str
    title: str
    html: str

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", self.html)).strip()


# (shortcut, what it does): the menus in main_window use exactly these
SHORTCUTS = [
    ("Ctrl/⌘ + N", "New project"), ("Ctrl/⌘ + O", "Open project…"), ("Ctrl/⌘ + S", "Save project"),
    ("Ctrl/⌘ + Shift + S", "Save project as…"), ("Ctrl/⌘ + W", "Close project (asks about unsaved changes)"),
    ("Ctrl/⌘ + Shift + O", "Add data files…"), ("Ctrl/⌘ + Alt + O", "Add data folder…"),
    ("Ctrl/⌘ + Shift + I", "Import a table from any instrument…"), ("Ctrl/⌘ + E", "Export report (PDF)…"),
    ("Ctrl/⌘ + Shift + E", "Export workbook (Excel)…"), ("Ctrl/⌘ + Shift + H", "Home (start page; the project stays open)"),
    ("Ctrl/⌘ + ,", "Settings"), ("F1", "This guide"), ("Ctrl/⌘ + Q", "Quit"),
]


def _table(rows: list[tuple[str, ...]], head: tuple[str, ...]) -> str:
    th = "".join(f"<th align='left'>{h}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table cellspacing='0' cellpadding='5' width='100%'><tr>{th}</tr>{body}</table>"


TOPICS: list[Topic] = [
    Topic("start", "Getting started", "Quick start", """
<h2>Quick start</h2>
<p>Echem Analyzer turns instrument files into <b>numbers you can defend</b>: sensitivity, limit of detection, linear range,
reproducibility, stability, selectivity and electrode properties, with the plots and a report to go with them.</p>
<ol>
<li><b>Load data.</b> Drop PalmSens <code>.csv</code> / <code>.pssession</code> files, a project, or a whole folder onto the window,
or use <i>Add data</i>. Data from other instruments comes in through <i>File ▸ Import table…</i>
(see <a href="topic:formats">Files and formats</a>).</li>
<li><b>Check the technique</b> of each curve in <b>Measurements</b>. It is recognised from the method id or the title; correct it
in the table if it is wrong.</li>
<li><b>Analyse.</b> Pick a measurement in <b>Analysis</b> (or <b>Standard addition</b> / <b>Voltammetry</b> for CA and CV runs) and
read the headline numbers and the plot. Settings are declared per analysis and remembered per measurement.</li>
<li><b>Send to the study.</b> <i>Add to study</i> puts the numbers that matter (peak height, Rct, a plateau…) in the
<b>Sensor study</b> table. Give each row a concentration, a condition (analyte / interferent), an electrode name, a time and a role.</li>
<li><b>Read the study:</b> calibration with LOD / LOQ and linear range, reproducibility between electrodes, stability over
time, selectivity and spike recovery.</li>
<li><b>Save and export.</b> A project (<code>.echemproj</code>) keeps data, settings and plots in one file; export a PDF report
or an Excel workbook from the same analysis. See <a href="topic:projects">Projects and exports</a>.</li>
</ol>
<p><b>Resize anything.</b> Every panel border has a grip (a column of dots between side panels and plots, a row of dots between a plot and
its table, and the edge of the Files panel). Drag it to make a panel larger or smaller; <b>double-click</b> it to reset that one;
<i>View ▸ Reset panel sizes</i> resets all of them. Your sizes are remembered between sessions.</p>
<p><b>Good to know.</b> Looking at something never counts as a change; only editing does. There is no autosave: save your project.
Every plot in a report is drawn on a white background in your chosen plot style and palette
(<i>Settings ▸ Plots</i>).</p>
"""),
    Topic("formats", "Getting started", "Files and formats", """
<h2>Files and formats</h2>
""" + _table([
        ("<code>.csv</code>", "PalmSens PSTrace export (UTF-16). Every curve in the file. CV exports carry <b>no scan rate</b>."),
        ("<code>.pssession</code>", "PSTrace session: every measurement including those never exported, plus the method (scan rate, vertices, step)."),
        ("<code>.echemproj</code>", "A project saved by this app: raw curves, settings, responses, plots, window state."),
        ("any table", "<i>File ▸ Import table…</i> reads CSV / TXT / TSV / XLSX with delimiter and decimal-comma detection, header and "
                      "units (mV, nA, kHz, kΩ… are converted to V, µA, Hz, Ω). For EIS pick Z′ and Z″ (or −Z″) columns."),
    ], ("Format", "Notes")) + """
<h3>Technique recognition</h3>
<p>First the instrument's method id, then words in the title (impedance, DPV, open circuit…). If a curve is misread, change its
technique in <b>Measurements</b>; the analyses offered follow.</p>
<h3>What has been verified</h3>
<p>The CA and CV readers were checked against real PalmSens exports. The readers for <b>DPV, SWV, LSV, EIS, OCP and chronocoulometry</b>
were built from the file format and checked on synthetic data with known answers; the EIS layout inside <code>.pssession</code>
is a best guess. If the first real file is misread, use the technique override or <i>Import table…</i>, and check the first
result by hand.</p>
"""),
    Topic("techniques", "Analyses", "Techniques and what is measured", """
<h2>Techniques and what is measured</h2>
""" + _table([
        ("CA · amperometry", "Standard addition (steps in an i–t trace), step response, steady state", "plateau per addition, sensitivity, C<sub>x</sub>, LOD"),
        ("CV", "Multi-cycle peaks, charge, cycle stability; scan-rate study", "i<sub>pa</sub>, i<sub>pc</sub>, E<sub>½</sub>, ΔE<sub>p</sub>, ECSA"),
        ("DPV · SWV · NPV · LSV", "Baseline-corrected peak analysis", "peak height, potential, FWHM, area"),
        ("EIS", "Equivalent-circuit fit (Randles ± CPE ± Warburg)", "R<sub>s</sub>, R<sub>ct</sub>, CPE, k⁰"),
        ("OCP · CP", "Step response and steady state", "level, noise, drift, t<sub>90</sub>"),
        ("CC · CA transients", "Cottrell / Anson", "area or diffusion coefficient"),
        ("any x–y table", "Peak or step analysis once the technique is chosen", "as above"),
    ], ("Technique", "Analysis", "Typical responses")) + """
<p>All of them end in <b>responses</b>, the currency of the <a href="topic:study">sensor study</a>.</p>
"""),
    Topic("sam", "Analyses", "Standard addition (CA)", """
<h2>Standard addition by chronoamperometry</h2>
<p>Steps in the current are detected automatically (a peak of the lagged difference of the median-filtered trace, above k robust σ and
<i>abrupt</i>, so smooth decays are not mistaken for additions). Drag a dashed marker to move a step, double-click to add one,
right-click to remove (turn the zoom/pan tool off first).</p>
<ul>
<li><b>Added concentration:</b> C = C<sub>s</sub>·ΣV / (V<sub>0</sub> + ΣV) with dilution correction, else C<sub>s</sub>·ΣV / V<sub>0</sub>.</li>
<li><b>Response per addition:</b> <i>plateau mean</i> (last W seconds before the next addition) or <i>drift-corrected</i>
(pre/post straight lines extrapolated to the step). The two can differ by 15–30 % in slope on real data: choose deliberately.</li>
<li><b>Fit:</b> signal against added concentration, baseline point included. <b>C<sub>x</sub></b> = intercept / slope with the usual
regression uncertainty. <b>LOD</b> = 3σ/S, <b>LOQ</b> = 10σ/S.</li>
<li>A background current biases C<sub>x</sub>; enter the blank current to correct for it.</li>
<li>Jumps in the <i>opposite</i> direction are drawn as grey dotted lines and reported, because they corrupt the plateau they fall in.</li>
</ul>
<p><b>Always enter your real V<sub>0</sub>, addition volumes and stock concentration.</b> The defaults are placeholders.</p>
"""),
    Topic("cv", "Analyses", "Cyclic voltammetry", """
<h2>Cyclic voltammetry with several cycles</h2>
<ul>
<li><b>Peaks per cycle:</b> anodic = maximum on the positive-going sweep, cathodic = minimum on the negative-going sweep (found from the
sign of the potential step, so any waveform works). Optionally restrict to a potential window and measure above a linear baseline.</li>
<li><b>Charge and capacitance</b> (needs the scan rate; read from a .pssession, typed in for CSV): Q = ∫ i dt per sweep and an apparent
capacitance (Q<sub>a</sub> − Q<sub>c</sub>)/(2ΔE). It also contains any Faradaic current: treat it as apparent.</li>
<li><b>Stability across cycles:</b> change of each peak relative to the previous cycle and the first cycle from which every change stays
below your tolerance. A peak that keeps rising means the electrode needs more conditioning cycles.</li>
<li>Cycle 1 often starts with a large charging transient; untick it (or <i>Skip first</i>) and do not trust its charge.</li>
</ul>
"""),
    Topic("peaks", "Analyses", "Peak analysis (DPV, SWV, LSV)", """
<h2>Peak analysis</h2>
<p>The baseline is drawn through the ends of the search window (linear or polynomial, fitted to the first and last few percent) and
subtracted. Reported: <b>peak height above the baseline</b>, raw peak current, <b>peak potential</b>, <b>FWHM</b> and <b>area</b>.</p>
<ul>
<li>Set a window around the peak of interest if there are several peaks or a steep wall near the end of the scan.</li>
<li>Direction is automatic; force it for noisy or weak peaks. Smoothing (Savitzky–Golay) is applied to the trace before the baseline and the peak are found, so it lowers the height of a sharp peak if the window is wide: keep it small compared with the peak width, or off.</li>
<li>A peak at the edge of the window is flagged: widen the window or check the baseline.</li>
</ul>
<p>For a calibration, send one <i>Peak height</i> per concentration to the study.</p>
"""),
    Topic("eis", "Analyses", "Impedance (EIS)", """
<h2>Electrochemical impedance spectroscopy</h2>
<p>Plots: Nyquist (Z′ vs −Z″) and Bode (|Z| and phase vs frequency). Convention in this app: Im(Z) is stored negative for a capacitive
response and the Nyquist plot shows −Im(Z) upwards.</p>
<ul>
<li><b>Circuits:</b> R<sub>s</sub>–(R<sub>ct</sub> ‖ CPE) (Randles with a constant-phase element), simple RC, and Randles with a
Warburg element. Fitting is modulus-weighted least squares from several starting points, so all frequencies count.</li>
<li><b>R<sub>ct</sub></b> is the charge-transfer resistance, usually the signal of an impedimetric sensor (it grows when the analyte or
a blocking layer binds). <b>CPE</b> parameters Q and n (n = 1 is an ideal capacitor); the effective capacitance uses the Brug formula.</li>
<li><b>k⁰</b> (apparent standard rate constant) from R<sub>ct</sub> needs the electrode area and probe concentration.</li>
<li>The fit quality is reported as a percentage; if it is large, try another circuit or check for drift during the scan.</li>
</ul>
"""),
    Topic("potentiometry", "Analyses", "Potentiometry and OCP", """
<h2>Open-circuit potential, potentiometry and step response</h2>
<ul>
<li><b>Steady state:</b> mean level of a stretch of a trace, its noise about the trend, drift (also in % per minute) and signal-to-noise.
Drift above 1 % per minute is flagged: the level may not be settled.</li>
<li><b>Step response (staircase):</b> the level after each step and the <b>response time t<sub>90</sub></b> (10→90 %, measured from the step
onset). For slow potentiometric responses set <i>Step shape</i> to <i>Allow slow responses</i>; the automatic choice expects abrupt steps for CA.</li>
<li><b>Nernstian response:</b> E vs log C should have a slope of 59.16/z mV per decade at 25 °C. Use the <i>log-linear</i> calibration model
and compare the slope with that value.</li>
</ul>
"""),
    Topic("cottrell", "Analyses", "Cottrell and Anson", """
<h2>Cottrell and Anson analysis</h2>
<p>Cottrell: i(t) = nFAC√D / √(πt), so |i| against t<sup>−½</sup> is a straight line through the origin. Anson: Q(t) = 2nFAC√(Dt/π) + Q<sub>dl</sub> + nFAΓ,
a line against t<sup>½</sup> whose intercept holds the double-layer and adsorbed charge. With the concentration and one of A or D known, the
other follows from the slope. Use <i>Window from / to</i> to skip the first moments (double-layer charging, instrument settling).</p>
"""),
    Topic("study", "Sensor study", "The sensor study", """
<h2>The sensor study</h2>
<p>Every analysis yields <b>responses</b>. The <b>Responses</b> table is where you say what each one <i>is</i>:</p>
""" + _table([
        ("Concentration + unit", "of the species named in Condition at which the measurement was made"),
        ("Condition", "“analyte”, or the name of an interferent (ascorbate, urate…)"),
        ("Electrode / group", "the sensor that produced it; needed for reproducibility"),
        ("Time", "days, hours or cycles since fabrication; needed for stability"),
        ("Role", "<b>standard</b> (calibration point), <b>blank</b> (replicate with no analyte, used for σ) or <b>sample</b> (an unknown or a spike)"),
        ("Use", "untick to leave a row out of every study"),
    ], ("Column", "Meaning")) + """
<p>Rows already in the table keep what you typed when you analyse again. Add rows by hand with <i>Add number…</i> (for numbers measured elsewhere)
and set many rows at once with <i>Set for selected…</i>.</p>
<p>The studies: <a href="topic:calibration">calibration</a>, <a href="topic:repro">reproducibility, stability, selectivity, recovery</a>.</p>
"""),
    Topic("calibration", "Sensor study", "Calibration and figures of merit", """
<h2>Calibration and figures of merit</h2>
<ul>
<li><b>Models:</b> linear, log-linear (Nernstian), Michaelis–Menten / Langmuir, Hill. “Automatic” picks the lowest AIC; all are shown with R²,
adjusted R², RMSE and AIC so you can see how close the call was.</li>
<li><b>Sensitivity</b> S = slope (response per concentration unit; per decade for log-linear; initial slope y<sub>max</sub>/K for Michaelis–Menten).</li>
<li><b>LOD</b> = k·σ/S (k = 3 by default, 3.3 in ICH style; set in <i>Settings ▸ Analysis</i>), <b>LOQ</b> = 10σ/S. σ is the standard deviation of blank
replicates when there are any, otherwise of the fit residuals. Blank replicates are far better: residual-based limits flatter a good fit.</li>
<li><b>Linear range:</b> the widest run of levels with R² ≥ 0.99 and each point within 10 % of the line (both adjustable). “Use only the linear range” restricts
S, LOD and LOQ to it.</li>
<li><b>Samples</b> are read from the fitted curve by inverse prediction with a standard error. A response outside what the model can invert is reported as such.</li>
</ul>
<p><b>Pitfalls the app warns about:</b> standards from several electrodes or several times pooled into one line (use the Electrode and Time
filters), different units, fewer than two concentrations, and log-linear fits that have no single LOD.</p>
"""),
    Topic("repro", "Sensor study", "Reproducibility and stability", """
<h2>Reproducibility, stability, selectivity and recovery</h2>
<ul>
<li><b>Reproducibility:</b> the same concentration on at least two electrodes (give each a Group name): mean, SD, <b>RSD %</b>, 95 % CI of the mean and Grubbs
outliers per level, plus the spread of the sensitivity between electrodes.</li>
<li><b>Stability:</b> one concentration measured over time: <b>retention %</b> relative to the first (or highest) measurement, drift per time unit,
exponential half-life if the response decays, and the time at which the linear trend reaches 90 %.</li>
<li><b>Selectivity:</b> response to each interferent next to the analyte: change vs the analyte alone (mixtures), interference % at equal
concentration, and the sensitivity ratio K = (y<sub>int</sub>/c<sub>int</sub>) / (y<sub>analyte</sub>/c<sub>analyte</sub>).</li>
<li><b>Recovery:</b> for spiked samples, found ÷ expected × 100 with the calibration chosen on the Calibration tab; 90–110 % is the usual window.</li>
</ul>
"""),
    Topic("electrode", "Sensor study", "Electrode characterisation", """
<h2>Electrode characterisation</h2>
<ul>
<li><b>Scan-rate study</b> (CV at several scan rates in a reversible redox probe): peak current against √ν gives the electroactive area through
Randles–Sevcik, i<sub>p</sub> = 0.4463·n·F·A·C·√(nFνD/RT). The log i<sub>p</sub>–log ν slope is 0.5 for diffusion control and 1 for adsorbed species.
<b>ECSA / geometric area</b> is the roughness factor. For surface-confined couples, <b>Laviron</b> analysis of the high-scan-rate points gives α and k<sub>s</sub>.</li>
<li><b>Double-layer capacitance:</b> CVs in a non-Faradaic window at several scan rates; half the anodic–cathodic current gap at a mid potential
against ν has slope C<sub>dl</sub>. With the geometric area and a specific capacitance you get the roughness factor.</li>
<li>Tick at least three runs and make sure each has its scan rate (a .pssession provides it; otherwise type it in the table).</li>
</ul>
<p>Send the results to the study to compare electrodes.</p>
"""),
    Topic("projects", "Projects and output", "Projects and exports", """
<h2>Projects, presets and exports</h2>
<ul>
<li><b>Project (.echemproj):</b> one file with the raw curves (it reopens even if the originals move), every setting, the responses, the plots as PNG and a CSV
snapshot of the results. A save writes a temporary file first, so a crash cannot damage an existing project. A damaged or hostile file is refused with a message.</li>
<li><b>Close project / Home:</b> <i>Close project</i> asks about unsaved changes and returns to the start page; <i>Home</i> shows the start page without closing anything.</li>
<li><b>Presets</b> are named protocols (stock, V<sub>0</sub>, windows…) kept in your config folder, never per-run data.</li>
<li><b>PDF report:</b> cover, summary, one page per analysed run, the sensor study, electrode studies and the analyses behind every response, with the setup of each.</li>
<li><b>Excel workbook:</b> summary with links, the Responses sheet (filterable), a sheet per study with an editable native calibration chart, per-analysis sheets and the raw numbers.</li>
<li>Exports always use a white background and the plot style, palette and resolution from <i>Settings</i>. Use <i>Publication</i> style for papers.</li>
</ul>
"""),
    Topic("shortcuts", "Reference", "Keyboard shortcuts", "<h2>Keyboard shortcuts</h2>" + _table(
        [(f"<b>{k}</b>", v) for k, v in SHORTCUTS], ("Shortcut", "Action"))),
    Topic("glossary", "Reference", "Glossary", """
<h2>Glossary</h2>
""" + _table([
        ("Sensitivity", "Slope of the calibration: response per unit concentration."),
        ("LOD / LOQ", "Smallest concentration distinguishable from the blank (kσ/S) / reliably quantifiable (10σ/S)."),
        ("Linear range", "Concentrations over which the response is linear within the chosen R² and deviation."),
        ("RSD", "Relative standard deviation, SD / mean × 100."),
        ("Retention", "Response at a time as a percentage of the first response."),
        ("t<sub>90</sub>", "Time for a response to go from 10 % to 90 % of its final change."),
        ("R<sub>ct</sub>", "Charge-transfer resistance: the diameter of the Nyquist semicircle."),
        ("CPE", "Constant-phase element, a non-ideal capacitor Z = 1 / (Q(jω)<sup>n</sup>)."),
        ("ECSA", "Electrochemically active surface area."),
        ("C<sub>dl</sub>", "Double-layer capacitance."),
        ("AIC", "Akaike information criterion: balances fit and number of parameters; lower is better."),
        ("Inverse prediction", "Reading an unknown concentration off a fitted calibration, with its uncertainty."),
        ("Nernstian slope", "59.16 / z mV per decade of activity at 25 °C."),
    ], ("Term", "Meaning"))),
    Topic("limits", "Reference", "Limits and troubleshooting", """
<h2>Limits and troubleshooting</h2>
<ul>
<li><b>A file is misread</b> (wrong technique, empty curve): change the technique in Measurements, or use <i>Import table…</i>. Unrecognised DPV / SWV / EIS / OCP exports
are the most likely, because their readers were built from the format, not from your files.</li>
<li><b>Calibration says “pooled”:</b> several electrodes or times are in one line. Choose an Electrode and a Time in the calibration settings.</li>
<li><b>No LOD:</b> you need ≥ 2 concentrations; for a log-linear model there is no single LOD.</li>
<li><b>Reproducibility empty:</b> give each electrode its own Group name and measure the same concentrations on each.</li>
<li><b>Scan-rate study empty:</b> tick ≥ 3 runs; each needs a scan rate and clear anodic and cathodic peaks.</li>
<li><b>Numbers look placeholder-like in standard addition:</b> V<sub>0</sub>, addition volumes and stock concentration default to placeholders.</li>
<li><b>Plots look different in the PDF:</b> reports always use white backgrounds and your plot style, not the on-screen dark theme.</li>
<li>Nothing is sent anywhere: the app works entirely on your computer.</li>
</ul>
"""),
]

_BY_ID = {t.id: t for t in TOPICS}


def topic(topic_id: str) -> Topic:
    return _BY_ID[topic_id]


def groups() -> list[tuple[str, list[Topic]]]:
    out: list[tuple[str, list[Topic]]] = []
    for t in TOPICS:
        if not out or out[-1][0] != t.group:
            out.append((t.group, []))
        out[-1][1].append(t)
    return out


def search(query: str) -> list[Topic]:
    """Topics whose title or text contains every word of ``query`` (all topics for an empty query)."""
    words = [w for w in query.lower().split() if w]
    if not words:
        return list(TOPICS)
    return [t for t in TOPICS if all(w in (t.title + " " + t.text).lower() for w in words)]


def links_in(html: str) -> list[str]:
    return re.findall(r'href="topic:([\w-]+)"', html)
