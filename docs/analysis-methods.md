# Analysis methods

What Echem Analyzer reads and how the numbers are computed. Each method is also in the in-app **Guide** (`F1`), with the formulas.

## What it reads

| Format | Notes |
|---|---|
| `.csv` | PSTrace export (UTF‑16). Every CA and CV curve in the file. **No scan rate** in CV exports. |
| `.pssession` | PSTrace session. Holds **every** measurement, including runs never exported to CSV, plus the method settings (scan rate, vertices, step). |
| `.echemproj` | A project saved by this app. |

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
