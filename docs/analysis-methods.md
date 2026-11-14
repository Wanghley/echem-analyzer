# Analysis methods

What Echem Analyzer reads and how the numbers are computed. Each method is also in the in-app **Guide** (`F1`), with the formulas.

## What it reads

| Format | Notes |
|---|---|
| `.csv` | PSTrace export (UTF‑16). Every CA and CV curve in the file. **No scan rate** in CV exports. |
| `.pssession` | PSTrace session. Holds **every** measurement, including runs never exported to CSV, plus the method settings (scan rate, vertices, step). |
| `.dta` *(preview)* | Gamry Framework. `CURVEn` tables are CV cycles; `ZCURVE` is impedance; `OCVCURVE` is open-circuit potential. The `TAG` line names the technique. |
| `.txt` *(preview)* | CH Instruments export (a date line, the technique, `Key = value` parameters, a `Potential/V, Current/A` line, rows), or an Autolab Nova export (one header line of column names). Told apart by their first lines. |
| `.mpt` *(preview)* | BioLogic EC-Lab ASCII export. The technique is taken from EC-Lab's own name for it; CV cycles from the `cycle number` column. |
| `.echemproj` | A project saved by this app. |

Every reader converts to the same units: current in µA, potential in V, time in s, impedance in Ω, with Im(Z) negative for a capacitive
spectrum. A file is recognised by what it contains, so a `.txt` from one instrument is not mistaken for another's. A CV that an instrument
wrote as one long trace is cut into scans at the reversal points of the potential. When a CV file does not state its scan rate, the median
|dE/dt| of its time and potential columns is used and the curve is marked as estimated. The four readers marked *preview* were written from the
manufacturers' file descriptions and tested on synthetic files only.

## Acceptance, yield and variants

* **Rules** (`acceptance.py`) bound a quantity per electrode: pass if min ≤ value ≤ max (inclusive); the value is the mean of the electrode's usable
  measurements, or tested for each (the worst one is shown), or the latest by time. An electrode fails if any rule fails, is incomplete if none failed but
  one is unmeasured, and passes otherwise.
* **Yield** = passed / (passed + failed) among judged electrodes, with the 95 % Wilson score interval
  p̃ ± z√(p(1−p)/n + z²/4n²) / (1 + z²/n), p̃ = (p + z²/2n)/(1 + z²/n); for 8 of 10 it is 49–94 %.
* **Variant comparison** (`variants.py`). Each electrode's sensitivity is the slope of its own straight-line calibration. A variant's mean is the mean of
  those slopes, its interval x̄ ± t<sub>0.975,n−1</sub>·s/√n (empty for one electrode), its RSD s/x̄. The pooled sensitivity, detection limit and linear range
  come from all the variant's standards in one calibration; reproducibility is the mean RSD between electrodes over the concentration levels; retention is the
  stability study's final retention; response time the median t90.
* **Fabrication trend.** A least-squares line of a per-electrode metric on a numeric parameter (`scipy.stats.linregress`), with r and the two-sided p-value;
  not computed for fewer than three electrodes or a parameter that does not vary.
* **Datasheet** (`datasheet.py`): the variant's fabrication parameters (mean ± SD of numbers), its figures of merit and its pooled calibration on one page, with
  the detection-limit factor k stated; one page also on US Letter.

## Response time, recovery, drift and temperature

* **t90 and t95.** The start level is the mean over the 5 s before the step onset; the end level the mean of the last window of the segment. A level
  is reached at the first crossing of a 3-point running median of the fraction (y − y₀)/(y₁ − y₀), interpolated between samples and measured from the
  onset. The fraction is signed, so a falling step gives its time to return exactly as a rising one gives its time to respond. For a first-order
  response t90 = 2.303 τ and t95 = 2.996 τ (verified on simulated traces).
* **Recovery.** With *Steps against the first* set to *Recovery steps*, the jumps opposite in direction to the first one are kept as steps and
  marked recovery; their times are the quantities *Recovery t90* and *Recovery t95*.
* **Baseline drift.** A least-squares line through the baseline up to 1 s (or the settle time, if larger) before the first detected step, at least 10 s
  and 10 points; y − slope·(t − t₀) is analysed and drawn, anchored at the start of the trace.
* **Temperature coefficient.** y = y<sub>ref</sub> + b·(T − T<sub>ref</sub>) by least squares; α = 100·b/y<sub>ref</sub> %/°C. Its standard error is from the delta method,
  var(α) = (100/y<sub>ref</sub>)²·[SE(b)² + (b/y<sub>ref</sub>)²·SE(a)² − 2(b/y<sub>ref</sub>)·cov(a, b)] with cov(a, b) = −x̄·s²/Sxx for x = T − T<sub>ref</sub>
  (about 95 % of 2·SE intervals cover the true α in simulation). Compensation: y<sub>c</sub> = y/(1 + α(T − T<sub>ref</sub>)/100).

## Ion-selective electrodes

`echem_analyzer/potentiometry.py`; switched on by *Ion-selective electrode* in the Calibration panel.

* **Theory.** E = E° + (2.303·RT/zF)·log₁₀ a; the slope is 59.16/z mV/decade at 25 °C and proportional to T. Activity a = γc with the Davies
  equation, log₁₀γ = −A z² (√I/(1+√I) − 0.3 I), A = 1.8248·10⁶ (ε<sub>r</sub>T)<sup>−3/2</sup> (≈ 0.51 at 25 °C).
* **Nernstian region.** With five or more concentration levels the curve is fitted as two straight lines in log₁₀ c, the split chosen by least squares;
  it is kept when AIC prefers it by more than 4 beyond the extra parameters, the calibration is not already straight (R² < 0.99999) and one line has
  less than half the other's slope. The steeper line is the Nernstian one. If the flat line is at low concentration it gives a detection limit; if at
  high concentration the response saturates and the region ends there.
* **Corner trimming.** The transition is a smooth corner, and its points bend both lines toward each other. Each level next to the corner is dropped while
  it lies farther than max(1 mV, 3σ) from the line through the others, with the line a Theil–Sen slope and a median-of-offsets intercept and σ from the
  median absolute deviation, so that one wrong standard cannot widen the tolerance. At least three levels stay in the Nernstian line and two in the flat one.
* **Detection limit (IUPAC).** The concentration at which the extrapolated flat part and the extrapolated Nernstian line cross. Not reported when they
  do not cross inside the range measured.
* **Standard potential.** E° at a = 1 M is intercept − slope·log₁₀(γ·f), with f the factor from the concentration unit to M.
* **Selectivity.** Nikolsky–Eisenman, E = E° + S log₁₀(aᵢ + Kᵖᵒᵗ aⱼ<sup>zᵢ/zⱼ</sup>). Separate solutions: log K = (E<sub>j</sub> − E<sub>i</sub>) zᵢF/(2.303RT) +
  log aᵢ − (zᵢ/zⱼ) log aⱼ. Fixed interference: log K = log a(crossing) − (zᵢ/zⱼ) log aⱼ.
* **Accuracy** on data generated from the Nikolsky–Eisenman equation (0.3 mV noise, 30 seeds): slope within 1 % of the true 59.16 and the detection limit
  within 25 % of the true value (the corner biases it slightly high).

## Calibration diagnostics and intervals

For linear and log-linear calibrations (`calibration.diagnostics`, `calibration.prediction_interval`):

* **Leverage** hᵢ = diag(X(XᵀX)⁻¹Xᵀ) with X = [1, x] (x = log₁₀ c for log-linear); it sums to 2.
* **Externally studentized residual** tᵢ = eᵢ / (s₍ᵢ₎√(1−hᵢ)), where s₍ᵢ₎ is the residual SD of the fit without point *i*. A point is an
  **outlier** when |tᵢ| exceeds the t limit with n−3 degrees of freedom at 0.05/(2n) (Bonferroni, so the whole set is wrongly flagged at most 5 %
  of the time). With replicates this is conservative on purpose.
* **Cook's distance** Dᵢ = rᵢ²hᵢ / (p(1−hᵢ)) with the internally studentized rᵢ and p = 2. A point is **influential** when Dᵢ > 1.
* **Inverse prediction interval.** For a response y₀ (mean of m readings) the interval is the set of x with
  (y₀ − ȳ − b(x − x̄))² ≤ t²s²(1/m + 1/n + (x − x̄)²/Sxx), solved as a quadratic in x (Fieller). It contains x₀ = (y₀ − a)/b, is exact for a straight
  line, and is empty when b² ≤ t²s²/Sxx (a slope not different from zero). Log-linear: the same in log₁₀ x, transformed back.
  Saturating models: x₀ ± t·SE with SE propagated from the residual scatter (approximate).
* Verified against deleting each point and refitting (Cook's D and tᵢ), against the band edges, and by simulation: 3000 calibrations, 95 % interval,
  95 % coverage.

## Bench calculators

`echem_analyzer/bench.py`; each is checked against a value worked out by hand.

* **Standard-addition plan.** The app's model is C = c<sub>s</sub>·ΣV/(V₀+ΣV). Inverting it, the cumulative stock volume for a target is
  ΣV = C·V₀/(c<sub>s</sub> − C), and each addition is the difference of successive totals. The additions stay under a fraction *p* of the cell
  when c<sub>s</sub> ≥ C<sub>max</sub>(1+p)/p (21 × the top target for 5 %).
* **Expected peak current.** Randles–Sevcik, reversible: i<sub>p</sub> = 0.4463·nFAC·√(nFνD/RT); totally irreversible: 0.4958·nFAC·√(αn<sub>a</sub>FνD/RT).
  The range is the smallest decade (1 nA … 100 mA) that holds 1.5 × the expected peak.
* **Nernst slope.** 2.303·RT/(|z|F), 59.16 mV/decade at 25 °C for z = ±1; it is proportional to T, so it moves by about 0.2 mV/decade per kelvin.
* **Ionic strength and Debye length.** I = ½ Σ c<sub>i</sub>z<sub>i</sub>²; λ<sub>D</sub> = √(ε<sub>0</sub>ε<sub>r</sub>k<sub>B</sub>T / (2N<sub>A</sub>e²I)) with ε<sub>r</sub>
  of water from the Malmberg–Maryott fit (78.3 at 25 °C): 0.96 nm at 0.1 M, 9.6 nm at 1 mM.

## Import checks

Every curve is checked when it is loaded (`echem_analyzer/dataqc.py`), and findings appear in the Measurements table and the report. They are
cautious heuristics that say *look at this*, never *this is wrong*:

* **Missing values, time order, flat line, too few points.** Time must increase strictly; a potential axis is exempt.
* **Clipped current.** The largest or smallest value repeated in a row for at least 8 points and 2 % of the trace. Currents only.
* **Spikes.** A point farther than 8 robust standard deviations (1.4826 × the median residual of the points that move) and 2 % of the range
  from the running median of 7 points. A step is followed by the median, so it is not a spike. The last point is compared with the line through
  the five before it (a running median cannot judge the last point; PalmSens often records a wrong one). The first point is not tested: a
  transient starts steeper than any line through what follows it.
* **Unsettled open-circuit potential.** Slope of the last fifth of the run above 1 mV/min, for runs of at least 20 s.
* **Implausible units.** Potentials beyond ±3 V, currents beyond 100 mA.
* **Impedance.** Frequencies must be positive and finite; five or more are needed for a fit.
* **Duplicates.** Curves of equal length and technique whose numbers agree to a relative 10⁻⁷ are reported on the later copy.

## CA standard addition

1. Pick a run in the **Chronoamperometry** tab; steps are detected automatically.
2. Check the markers: drag a dashed line to move it, double-click to add, right-click to remove (turn the toolbar's zoom/pan off first).
3. Fill in **Additions**: stock concentration, cell volume V₀, volume per addition. Individual volumes and concentrations are editable in the table.
4. Read the headline tiles (sensitivity, C_x, LOD, R²) and the **Results** card, plus the calibration plot and residuals.

* **Inputs, each with a unit selector** (a selector converts what is shown, never the quantity): stock concentration, V₀ (total cell volume),
  sample volume (V₀/2 for a 1:1 mix), volume per addition and the pipette-**verified volume**, background current, electrode area (reporting only).
* **Added concentration:** `C_n = c_s·n·v / (V₀ + n·v)`. **One dilution correction only** (*Dilution*): *concentration* (this formula),
  *signal scaling* (`x = c_s·ΣV/V₀`, signal × `(V₀+ΣV)/V₀`, which is exact, also for the sample's own dilution) or *none*; never both.
* **Signal:** polarity-corrected plateau `|i|` minus the background.
* **Response per addition:** *plateau mean* (settle time, averaging window, end guard) or *drift-corrected*. **The two can give
  materially different slopes on real data.**
* **Fit:** `y = a + b·x`, baseline point included or not (or both, side by side). `x₀ = −a/b`, **C_x = |x₀|**, **C_sample = C_x·V₀/V_sample**, with the
  95 % interval `t(0.975, N−2)·s_x0`, `s_x0 = (s_y/|b|)·√(1/N + ȳ²/(b²·Sxx))`, and the **suitability** `b·√Sxx/s_y` (flagged below 12 by default).
* **LOD = 3.3σ/|b|, LOQ = 10σ/|b|** (factors adjustable); σ from the residual s(y/x) (default), the baseline noise or the SD of blank
  replicates, all three always listed.
* **Warnings** (never blocking): zero background, sample volume above V₀, dilution off or double, an implausible result for the stock unit,
  patterned residuals, failed suitability, baseline-noise LOD far below the s(y/x) LOD, a point count that does not match the additions,
  settle + window longer than a segment. All settings, units included, are saved in presets and projects.
* **Step detection:** a peak of the lagged difference of the median-filtered trace, above `k` robust σ and *abrupt*
  (≥ 25 % of its change between two consecutive samples), so smooth decays are not mistaken for additions.
  Jumps in the *opposite* direction are never dropped silently: they are drawn as a dotted grey `!` line and reported,
  because they corrupt the plateau of the segment they fall in.
* A background of 0 µA shifts C_x by background / slope: measure a blank at the same potential and time and enter it.

## CV with several cycles

The **Voltammetry** tab has two parts.

**Cycle analysis** works on one *run* (all the cycles measured together):

* The plot shows the cycles light to dark so the evolution reads at a glance; optionally the negative-going sweep is
  dashed, peaks are marked on the raw curve, excluded cycles are shown faintly, and *Zoom to features* ignores the
  start-up charging current that would otherwise flatten everything.
* **Peaks per cycle:** anodic = maximum on the positive-going sweep, cathodic = minimum on the negative-going sweep
  (works for any waveform: sweeps are found from the sign of the potential step). Optionally restrict the search to a
  potential **window** and measure the height above a **linear baseline** drawn across it. The baseline is only applied
  to a window you have set: across a whole sweep it would be a meaningless diagonal.
* **Charge and capacitance** (needs the scan rate; read from `.pssession`, typed in for CSV): `Q = ∫ i dt` per sweep and an
  apparent capacitance `(Q_a − Q_c) / (2ΔE)`. It also contains any Faradaic current, so treat it as an apparent value.
* **Headline tiles:** cycles used, last anodic peak, last change, and a *Stable / Settling* status.
* **Stability across cycles:** the change of each peak relative to the previous cycle, the total drift, and the first cycle
  from which every change stays below your tolerance (default 1 %). The summary says plainly when the peaks are still moving,
  i.e. the electrode may need more conditioning cycles.
* **Cycle selection:** untick cycles in the table, or use *All / Skip first / First + last / Last only*. Cycle 1 often begins
  with a start-up current far larger than the signal; the summary flags it and its charge should not be trusted.
* *Export cycle table…* writes one row per cycle (excluded ones flagged).

**Overlay** plots any cycles or curves from any files together (e.g. two electrodes), with a peak table and CSV export.

## Compare

Overlays CA traces and calibrations of several runs with a summary table. By default only runs that look like a staircase
and are not duplicates (the same run is often in both the `.csv` and the `.pssession`) are ticked.
