"""The in-app guide (no Qt): what every analysis does, the formulas behind the numbers, shortcuts and limits.

Each topic is a little HTML (the subset Qt's text browser renders).  Links between topics use ``topic:<id>``.
Keeping it here, away from the widgets, lets tests check that every link resolves and every shortcut is real.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import dataqc


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
    ("Ctrl/⌘ + Shift + I", "Import a table from any instrument…"), ("Ctrl/⌘ + Shift + B", "Bench calculators…"),
    ("Ctrl/⌘ + E", "Export report (PDF)…"),
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
<li><b>Load data.</b> Drop instrument files (PalmSens, Gamry, CH Instruments, BioLogic, Autolab), a project, or a whole folder onto
the window, or use <i>Add data</i>. Any other table of numbers opens the import dialog, where you choose the columns
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
        ("<code>.dta</code> <i>(preview)</i>", "Gamry Framework: CV (one curve per cycle), LSV, DPV, SWV, chronoamperometry, open circuit and EIS."),
        ("<code>.txt</code> <i>(preview)</i>", "CH Instruments text export (CV is cut into scans at its reversal points), or an Autolab Nova export. "
                                                "The program tells them apart by their first lines."),
        ("<code>.mpt</code> <i>(preview)</i>", "BioLogic EC-Lab ASCII export: CV (cycles from the cycle number), chronoamperometry, open circuit and EIS."),
        ("<code>.echemproj</code>", "A project saved by this app: raw curves, settings, responses, plots, window state."),
        ("any table", "A table no reader recognises (CSV / TXT / TSV / XLSX) opens the import dialog by itself, or use <i>File ▸ Import table…</i>. "
                      "It detects the delimiter, decimal comma, header and units (mV, nA, kHz, kΩ… are converted to V, µA, Hz, Ω). "
                      "For EIS pick Z′ and Z″ (or −Z″) columns."),
    ], ("Format", "Notes")) + """
<h3>Technique recognition</h3>
<p>First the instrument's method id, then words in the title (impedance, DPV, open circuit…). If a curve is misread, change its
technique in <b>Measurements</b>; the analyses offered follow.</p>
<h3>What has been verified</h3>
<p>The CA and CV readers were checked against real PalmSens exports. The readers for <b>DPV, SWV, LSV, EIS, OCP and chronocoulometry</b>
were built from the file format and checked on synthetic data with known answers; the EIS layout inside <code>.pssession</code>
is a best guess. If the first real file is misread, use the technique override or <i>Import table…</i>, and check the first
result by hand.</p>
<p>The readers marked <b>preview</b> (Gamry, CH Instruments, BioLogic, Autolab) were written from the manufacturers' file
descriptions and checked only on synthetic files. Open the first real file of each kind next to the instrument's own software and
compare a curve; a CV whose scan rate the file does not state gets one estimated from its time and potential columns, and says so.</p>
"""),
    Topic("bench", "Getting started", "Bench calculators", """
<h2>Bench calculators</h2>
<p><i>Tools ▸ Bench calculators…</i> (<b>Ctrl/⌘ + Shift + B</b>) holds the arithmetic done on paper at the bench. Every page recomputes
as you type, and says in plain words what is missing or cannot be right. The window is built once, so what you typed is still there
when you open it again.</p>
<ul>
<li><b>Dilution.</b> C₁·V₁ = C₂·V₂: fill any three boxes. Both concentrations share a unit, both volumes another.</li>
<li><b>Serial dilution.</b> Tubes diluted from one another by a constant factor, each ending at the volume you give.</li>
<li><b>Standard addition.</b> The stock volumes to pipette so the cell reaches each target concentration. It is the exact inverse of the
model the standard-addition analysis uses, C = c<sub>s</sub>·ΣV/(V₀+ΣV), so entering these volumes in the analysis gives your targets back.
Volumes can be rounded to what your pipette sets (the concentration actually reached is then recalculated), and it warns when an addition is
smaller than the pipette's range or when the additions dilute the matrix by more than 5 %. <i>Copy table</i> puts the plan on the clipboard.</li>
<li><b>Expected signal.</b> The Randles–Sevcik peak current of a couple at a scan rate, and the smallest decade current range that holds it with
50 % headroom. Too small a range clips the peak; too large a range loses resolution. The table lists several scan rates, for a scan-rate study.</li>
<li><b>Nernst slope.</b> 2.303·R·T/(z·F) per decade for the charge you give, the potential change between two concentrations, and how the slope
moves with temperature, which matters for a sensor worn on skin.</li>
<li><b>Ionic strength.</b> I = ½ Σ cᵢzᵢ² and the Debye length of the solution. A field-effect or label-free sensor only senses binding within a few
Debye lengths of its surface, which is why a sample diluted for the measurement can respond differently from the same sample neat. Salts are
taken as fully dissociated, so phosphate buffers are approximate.</li>
<li><b>Weighing.</b> Milligrams to dissolve for a given concentration, volume and molar mass.</li>
</ul>
"""),
    Topic("checks", "Getting started", "Import checks", f"""
<h2>Import checks</h2>
<p>Every curve is looked over when it is loaded, before any number is computed from it. The <b>Checks</b> column of
<b>Measurements</b> says <i>OK</i> or how many errors and warnings a curve has; hover it, or select the curve, to read them. The same
findings are listed in the report's Overview. A finding means <i>look at this</i>, never <i>this is wrong</i>: the thresholds are
deliberately cautious, and nothing is removed or changed for you.</p>
""" + _table([
        ("Points that are not numbers", "Missing values in the file. An <b>error</b> when more than half of the trace is missing."),
        ("Time runs backwards or repeats", "<b>Error</b>: step times, rates and drift cannot be trusted. Not applied to a potential axis, which turns by design."),
        ("Current stuck at one value", f"The largest or smallest value repeated in a row (at least {dataqc.SATURATION_MIN_RUN} points and "
                                       f"{100 * dataqc.SATURATION_MIN_FRACTION:g} % of the trace): the current range was probably too small. Currents only."),
        ("Isolated spike", f"A point more than {dataqc.SPIKE_SIGMAS:g} robust standard deviations and {100 * dataqc.SPIKE_MIN_FRACTION:g} % of the "
                           "range away from its neighbours. A step, a trend and the steep start of a transient are not spikes. The last point is "
                           "judged against the line through the five before it, because PalmSens often records a wrong last point."),
        ("Flat line, too few points", f"No change at all, or fewer than {dataqc.MIN_POINTS} usable points."),
        ("Potential not settled", f"An open-circuit potential still moving by more than {dataqc.DRIFT_LIMIT_MV_PER_MIN:g} mV/min over its last fifth "
                                  f"(runs of at least {dataqc.DRIFT_MIN_SECONDS:g} s): the reading depends on when you stop."),
        ("Implausible units", f"Potentials beyond ±{dataqc.POTENTIAL_LIMIT_V:g} V, or currents beyond {dataqc.CURRENT_LIMIT_UA / 1e3:g} mA: millivolts "
                              "read as volts, or nanoamps as microamps."),
        ("Impedance", "Zero or negative frequencies, missing values, or fewer than five frequencies."),
        ("Same data twice", "A curve whose numbers match an earlier one (to seven digits): loaded twice, or one run saved under two names."),
    ], ("Check", "What it looks for")) + """
<p>The checks run again when you change a measurement's technique, because some depend on it (the potential check applies to open-circuit
potential only). They were tuned on synthetic data with known faults; if a real run is flagged for no good reason, or a fault goes
unflagged, that is worth knowing.</p>
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
right-click to remove (turn the zoom/pan tool off first). The same calculation is available to any other part of the app that has
(added concentration, signal) pairs.</p>
<h3>Inputs (each with a unit selector; a selector converts the number shown, never the quantity)</h3>
<ul>
<li><b>Stock</b> concentration and unit; <b>V<sub>0</sub></b>, the total cell volume before any addition; <b>sample volume</b>, the part of V<sub>0</sub>
that is sample (smaller than V<sub>0</sub> when it is mixed with buffer or reagent: 1:1 means V<sub>0</sub>/2);
<b>volume per addition</b> (one value, or one per step in the table) and the <b>verified volume</b> a pipette check gave, which replaces the nominal one.</li>
<li><b>Background current</b> from a blank run at the same potential and time. It is subtracted from |i|; left at 0 the result carries a warning,
because C<sub>x</sub> is then shifted by background / slope.</li>
<li><b>Response:</b> plateau mean (settle time, averaging window, end guard) or drift-corrected. The two can differ by 15–30 % in slope on real data.</li>
</ul>
<h3>Calculation</h3>
<ul>
<li>Concentration after n additions: <b>C<sub>n</sub> = c<sub>s</sub>·n·v / (V<sub>0</sub> + n·v)</b>.</li>
<li><b>One dilution correction only</b> (Dilution box): <i>Concentration</i> (the formula above), <i>Signal scaling</i> (x = c<sub>s</sub>·ΣV/V<sub>0</sub> and the signal
multiplied by (V<sub>0</sub>+ΣV)/V<sub>0</sub>; this form is exact, also for the dilution of the sample itself) or <i>None</i>. They are alternatives:
using both would correct the dilution twice. With the additions above about 2 % of V<sub>0</sub>, prefer signal scaling.</li>
<li>Fit y = a + b·x with x = C<sub>n</sub> and y = signal − I<sub>bkg</sub>. <b>x<sub>0</sub> = −a/b</b>, <b>C<sub>x</sub> = |x<sub>0</sub>|</b> (the analyte in the cell),
<b>C<sub>sample</sub> = C<sub>x</sub>·V<sub>0</sub>/V<sub>sample</sub></b>.</li>
<li><b>Uncertainty:</b> s<sub>x0</sub> = (s<sub>y</sub>/|b|)·√(1/N + ȳ²/(b²·S<sub>xx</sub>)); the 95 % interval is t(0.975, N−2)·s<sub>x0</sub>, scaled by V<sub>0</sub>/V<sub>sample</sub> for C<sub>sample</sub>.</li>
<li><b>Suitability</b> b·√S<sub>xx</sub>/s<sub>y</sub> (flagged below your threshold, default 12): how firmly the points pin down the extrapolation.</li>
<li><b>LOD = 3.3σ/|b|</b>, <b>LOQ = 10σ/|b|</b> (both factors are adjustable). σ comes from the <i>residual s(y/x)</i> (default), the <i>baseline noise</i> of the trace,
or the <i>SD of blank replicates</i>; all three, with their LODs, are always listed.</li>
<li>Include or exclude the baseline (zero-addition) point, or tick <i>Also show the result with the baseline point switched</i> to see both.</li>
</ul>
<h3>Warnings (shown in the results; they never block the calculation)</h3>
<p>background is 0 · sample volume larger than the cell · dilution off with a non-trivial added volume (or concentration mode with a large one) ·
a result above your plausible maximum, or larger than the stock itself (wrong unit?) · residuals that curve or follow a pattern · suitability below the threshold ·
baseline-noise LOD far below the s(y/x) LOD · a fit whose points do not match the additions plus baseline · settle time + window longer than a segment.</p>
<p>All settings can be saved as a <b>preset</b> (units included) and reapplied to other runs. <b>Always enter your real V<sub>0</sub>, volumes and stock concentration:</b> the defaults are placeholders.</p>
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
<li><b>Step response (staircase):</b> the level after each step and the <b>response times t<sub>90</sub> and t<sub>95</sub></b> (to 90 % and 95 % of the
change, measured from the step onset; a level counts as reached when a 3-point running median crosses it, so one noisy sample does not). For slow
potentiometric responses set <i>Step shape</i> to <i>Allow slow responses</i>; the automatic choice expects abrupt steps for CA. See also
<a href="topic:wearable">Wearable sensing</a> for recovery times and baseline drift.</li>
<li><b>Nernstian response:</b> E vs log C should have a slope of 59.16/z mV per decade at 25 °C. For an ion-selective electrode switch on
<b>Ion-selective electrode (potentiometric)</b> in the Calibration panel (below).</li>
</ul>
<h3>Calibrating an ion-selective electrode</h3>
<p>Send the steady-state potential of each standard to the study, give the standards their concentrations, then tick <i>Ion-selective electrode</i> and
set the ion's <b>charge</b> (signed: +1 for Na⁺, −1 for Cl⁻), the <b>temperature</b> and, if the standards sit in a constant ionic background (an
adjuster such as ISA), its <b>ionic strength</b>. The app then</p>
<ul>
<li>tries the curve as a <b>flat part plus a Nernstian line</b> in log₁₀ c and keeps that only if it fits clearly better than one line;</li>
<li>leaves the <b>curved corner</b> between the two out of both lines, since it bends a true 59 mV/decade slope to about 57;</li>
<li>reports the <b>slope</b> as a percentage of 2.303·R·T/(z·F) for that charge and temperature, and says so when it is sub-Nernstian (an ageing or
fouled membrane, or interference), super-Nernstian (usually a wrong charge) or of the wrong sign;</li>
<li>gives the <b>IUPAC detection limit</b>, the concentration where the extrapolated flat part and the extrapolated line cross, and the limits of the
Nernstian region. If the response is straight to the lowest standard there is no limit to read off and it says the limit lies below the range;</li>
<li>gives the <b>standard potential E°</b> at an activity of 1 M. With an ionic strength it uses the Davies activity coefficient (good to about 0.5 M);
without one, concentration is taken as activity;</li>
<li>describes each unknown by where it falls: <i>below the detection limit</i> (report it as below the limit, not as a value), <i>in the curved foot</i>
below the Nernstian region (approximate), or <i>above the region</i> (an extrapolation). Unknowns are read from the Nernstian line only.</li>
</ul>
<p>The potential must be in mV or V; volts are converted. The <b>selectivity coefficient</b> log K<sup>pot</sup> of the Nikolsky–Eisenman equation by the
separate-solution and fixed-interference methods is available to scripts as <code>echem_analyzer.potentiometry.log_k_ssm</code> and
<code>log_k_fim</code>.</p>
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
<li><b>Samples</b> are read from the fitted curve by inverse prediction with a standard error and a <b>95 % interval</b>. For a straight line the interval
is found by inverting the prediction band, so it is exact and, when the line is poorly determined, wider on one side; if the slope cannot be told from zero
there is no finite interval. For the saturating models it is x ± t·SE and approximate. A response outside what the model can invert is reported as such.</li>
<li><b>Standards to check.</b> For linear and log-linear fits each standard is tested for how far it sits from the line without it (an externally studentized
residual, beyond a Bonferroni-corrected t limit, so a clean calibration is flagged in about 5 % of cases at most) and for how far it moves the line (Cook's
distance above 1). Flagged standards are ringed on the plot, named in the warnings and listed with the reason. Nothing is removed for you: look at the
measurement, then untick it in the Responses table if it is wrong.</li>
</ul>
<p><b>Pitfalls the app warns about:</b> standards from several electrodes or several times pooled into one line (use the Electrode and Time
filters), different units, fewer than two concentrations, and log-linear fits that have no single LOD.</p>
"""),
    Topic("wearable", "Sensor study", "Wearable sensing: recovery, drift, temperature", """
<h2>Wearable sensing: recovery, drift and temperature</h2>
<p>A sensor worn on skin or sitting in a flowing sample is judged on more than its calibration: how fast it answers and returns, how much its
baseline wanders, and how its response moves with temperature.</p>
<h3>Response and recovery time</h3>
<p>The <b>Step responses (additions)</b> analysis reports t<sub>90</sub> and t<sub>95</sub> for every step. By default a jump the other way is treated as an
artefact and only reported (that is right for additions). If your sensor is washed out, set <i>Steps against the first</i> to <b>Recovery steps</b>: those
steps are then marked <i>recovery</i> in the table and their times go to the study as <b>Recovery t90</b> and <b>Recovery t95</b>, separate quantities
from the response times. Nothing is called a recovery unless you ask for it.</p>
<h3>Baseline drift</h3>
<p><i>Baseline drift ▸ Remove a straight-line drift</i> fits a straight line to the baseline before the first step (at least 10 s and 10 points) and
subtracts it from the whole trace, so the plateaus show the change the analyte made. It assumes the drift is linear and carries on at the same rate:
look at the corrected trace, and do not use it if the baseline settles exponentially. If the baseline is too short it says so and leaves the trace alone.
The steady-state analysis does not offer this on purpose: it exists to measure drift.</p>
<h3>Temperature coefficient</h3>
<p>Measure the same standard at several temperatures and fill the <b>T (°C)</b> column of the Responses table (the <i>Set for selected…</i> dialog fills many
rows at once). The <b>Temperature</b> tab (the last Study tab) then fits the response as a straight line in T − T<sub>ref</sub>. Its intercept is the response at
the reference temperature, and the <b>coefficient α</b> is slope ÷ intercept in % per °C, with its uncertainty. Each response divided by 1 + α·(T − T<sub>ref</sub>)
is what it would have read at the reference temperature; the RSD before and after shows what compensating buys. Choose one concentration, since a
percentage coefficient only makes sense at one. It warns about pooled concentrations or electrodes, only two temperatures (a line always fits two points),
a narrow range, a reference outside the range, and a coefficient not clearly different from zero.</p>
"""),
    Topic("prototypes", "Sensor study", "Prototypes: devices, acceptance, variants", """
<h2>Prototypes: devices, acceptance and variants</h2>
<p>To design a sensor you build several electrodes of each version and ask which version is better, how many of them are good, and which step of the
fabrication matters. Three Study tabs do this; they use the electrode names you already give in the <i>Group</i> column of the Responses table.</p>
<h3>Devices</h3>
<p>One row per electrode: its <b>variant</b> (a design or recipe), <b>lot</b> (a fabrication batch), date, notes, and any <b>fabrication parameters</b> you add
by name (ink volume, deposition time, annealing temperature…). A number typed in a parameter is kept as a number, so it can be plotted. <i>Import CSV…</i>
and <i>Export CSV…</i> exchange the table with a lab notebook (any delimiter and column order; a column called <code>id</code> names the electrode).</p>
<h3>Acceptance rules and yield</h3>
<p>A rule bounds a measured quantity: <i>ΔEp ≤ 80 mV</i>, <i>peak-current ratio between 0.9 and 1.1</i>. A device passes when every rule holds, fails (and
says which rule and what it measured) or is <i>not measured</i>. <i>Judged by</i> chooses what is tested when a device has several measurements: the mean,
every one, or the latest. The <b>yield</b> of each variant or lot is passed ÷ (passed + failed), with a 95 % Wilson interval, because “8 of 10” is not as
certain as “80 of 100”; devices that could not be judged are not counted either way. Load the ferri/ferrocyanide template as a start, and save your own
sets to reuse in any project. To get ΔEp and the peak ratio into the study, run <b>Redox couple</b> on a CV scan of each electrode in the Analysis tab and
press <i>Add to study</i>.</p>
<h3>Variants</h3>
<p>Each electrode is calibrated on its own, so a variant is described by how its electrodes <i>differ</i>: the mean sensitivity with a 95 % interval from the
spread between electrodes, next to the pooled detection limit and linear range, the reproducibility, retention, median response time and yield.
Choose a fabrication parameter to plot against sensitivity (or detection limit) with a line, its correlation and p-value: that is how you see which
step drives performance. With only three or four electrodes the p-value will be large; it is shown so you can see how little they can say.
<i>Export datasheet (PDF)…</i> writes one page per variant.</p>
"""),
    Topic("automation", "Projects and output", "Command line and data package", """
<h2>Command line and data package</h2>
<p>Everything that writes a file can run without a window, from a script or a batch job:</p>
""" + _table([
        ("<code>echem-analyzer report PROJECT</code>", "PDF report of a project (<code>-o</code> to name it)"),
        ("<code>echem-analyzer workbook PROJECT</code>", "Excel workbook"),
        ("<code>echem-analyzer datasheet PROJECT -q Quantity -v Variant</code>", "one-page datasheet per variant"),
        ("<code>echem-analyzer package PROJECT</code>", "data package (<code>--no-raw</code> leaves the instrument files out)"),
        ("<code>echem-analyzer qc FILE_OR_FOLDER …</code>", "check instrument files; <code>--strict</code> counts warnings, <code>--json</code> prints JSON"),
        ("<code>echem-analyzer convert FILE</code>", "any supported file as a tidy CSV in the app's units (<code>-o -</code> for standard output)"),
    ], ("Command", "What it does")) + """
<p>Exit codes: <b>0</b> done (or no problems found), <b>1</b> <code>qc</code> found problems, <b>2</b> an input could not be used, with the reason on standard
error. Commands that draw a PDF start Qt without a display. The Windows build is a windowed program and prints nothing to a console; use a source or pip
install there.</p>
<h3>The data package</h3>
<p><i>File ▸ Export data package…</i> (or <code>package</code>) writes a zip another person can read without this program: the <b>original instrument files</b>
with SHA-256 checksums, every curve as a CSV with the unit of each column in its header and the exact numbers, the study's responses, the devices and
acceptance rules, the analysis settings (so the analysis can be repeated), a readable results snapshot, a machine-readable <code>metadata.json</code>, and a
<code>README.md</code> that says what is where, in which units and how the numbers were obtained. An original file that has since moved is noted, and its
curves are still included.</p>
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
