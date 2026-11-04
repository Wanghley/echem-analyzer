# Echem Analyzer

Desktop app for PalmSens PSTrace exports. It analyses chronoamperometry (CA) standard additions and
multi-cycle cyclic voltammetry (CV), compares runs and electrodes, keeps everything in projects, and turns an
analysis into a **PDF report** or an **Excel workbook** that contain the data, setup, results and plots together.

## Platform (v0.4): any measurement, not only standard addition

Besides CA standard addition and multi-cycle CV the app now handles **DPV / SWV / NPV / LSV, EIS, OCP / CP, chronocoulometry**
and any x–y table (*Measurements ▸ Import table…*). Workflow: **Measurements** (technique recognised from the method id or
title; correct it in the table if wrong) → **Analysis** (peak height, equivalent-circuit fit, step response, steady state,
Cottrell/Anson) → *Add to study* → **Sensor study** (Responses table; calibration with model comparison, LOD/LOQ and linear
range; reproducibility between electrodes; stability over time; selectivity; spike recovery). **Electrode** does scan-rate
(Randles–Sevcik ECSA, Laviron) and Cdl studies from CV series. PDF, Excel and project snapshots include all of it.

**Not verified on real files:** the DPV/SWV/LSV/EIS/OCP/CC parsers and PalmSens method ids were built from the format and
tested on synthetic data with known answers only; the EIS layout of `.pssession` is a best guess. Use the technique override
and *Import table…* if a file is misread, and check the first real result by hand.

## Resizable panels

Every panel border has a visible grip: drag the one between a plot and its settings panel (right) or its table (below), or the edge of the
Files panel (left), to make it larger or smaller. **Double-click** a grip to reset that panel; *View ▸ Reset panel sizes* resets them all.
Sizes are remembered between sessions (per user, not per project).

## Guide and Settings

* **Guide** (`F1`, the ? button, or a tile on the start page): a searchable in-app manual with the methods and formulas behind every number
  (standard addition, peaks, EIS, potentiometry, calibration and LOD/LOQ, reproducibility, stability, selectivity, electrode studies),
  the keyboard shortcuts, a glossary and troubleshooting.
* **Settings** (`Ctrl/⌘+,` or the gear button), applied at once and saved in your user folder:
  *Plots*: **modern** or **publication** style (boxed axes, inward and minor ticks, serif text), **colour-blind-safe** (Okabe–Ito) or
  **greyscale** colours, text size, with a live preview; *Analysis*: LOD factor k (3 or 3.3) and default concentration unit;
  *Reports*: analyst and institution on the cover, figure resolution (150–600 dpi), A4 / Letter; *Files*: recent projects, reset.
  Exports always use a white background with your plot style and palette.

## Install (macOS, Windows, Debian/Ubuntu)

Download the installer for your system from the [Releases](https://github.com/Wanghley/echem-analyzer/releases) page.
`SHA256SUMS.txt` lets you verify the download (`shasum -a 256 -c SHA256SUMS.txt`).

| System | File | Install |
|---|---|---|
| macOS, Apple silicon (M1 and later) | `Echem-Analyzer-<version>-macos-arm64.dmg` | open it and drag **Echem Analyzer** onto *Applications* |
| macOS, Intel | `Echem-Analyzer-<version>-macos-x86_64.dmg` | the same |
| Windows 10/11 (64-bit) | `Echem-Analyzer-<version>-windows-x64-setup.exe` | run it (no administrator needed; it can install for all users) |
| Windows, no install | `Echem-Analyzer-<version>-windows-x64-portable.zip` | unzip and run `EchemAnalyzer.exe` |
| Debian, Ubuntu and derivatives (64-bit) | `echem-analyzer_<version>_amd64.deb` | `sudo apt install ./echem-analyzer_<version>_amd64.deb` |

Double-clicking a `.echemproj` project opens it in the app on all three systems. On Linux the program is `echem-analyzer`
(and appears in the applications menu); remove it with `sudo apt remove echem-analyzer`. The Linux package is built on Ubuntu 22.04,
so it needs Ubuntu 22.04 / Debian 12 or newer.

**The installers are not signed**, so the first launch shows a warning that does not mean anything is wrong with the file:

* **macOS:** *"cannot be opened because the developer cannot be verified"*. Control-click the app, choose **Open**, then **Open** again; or
  on recent macOS open *System Settings ▸ Privacy & Security* and press **Open Anyway**; or run
  `xattr -dr com.apple.quarantine "/Applications/Echem Analyzer.app"`.
* **Windows:** *"Windows protected your PC"* (SmartScreen). Press **More info**, then **Run anyway**.

To remove the warnings the release has to be signed (an Apple Developer ID plus notarization for macOS, a code-signing certificate for
Windows); see *Building the installers* below.

## Launch from the source

Double-click **`run.command`**, or from a terminal:

```sh
./run.command                              # empty window
./run.command "<project, files or folder>"  # open straight away; a .echemproj opens as a project
```

The first launch downloads Python 3.12 and the libraries through [uv](https://docs.astral.sh/uv/)
(`brew install uv` if missing). The virtual environment lives in `~/.cache/echem-analyzer/venv`,
**outside Google Drive**, so it is not synced. You can also drag files, folders or a project onto the window.

## Projects: save your work

A **project** is one self-contained `.echemproj` file (a zip archive) holding:

* the **raw curves** of every loaded file, embedded, so the project reopens even if the original CSV /
  pssession files have moved or been deleted;
* **every analysis setting**: step times, volumes, excluded points, CV windows, excluded cycles, …;
* **the plots** of every analysed run, as PNG files under `plots/` (the same figures the PDF contains);
* the **state of the window**: which tab, which run, which curves are ticked;
* **project info** (name, operator, electrode, analyte, matrix, notes);
* a **readable CSV snapshot of the results** (`results/` inside the archive; open it with any zip tool).
  Results and plots are rewritten on every save and never read back: they are always recomputed from the data and
  settings. (Figures are cached, so saving again after a small change is fast.)

| Menu (File) | |
|---|---|
| New project / Open project… / Open recent | `Ctrl/⌘+N`, `Ctrl/⌘+O` |
| Save project / Save project as… | `Ctrl/⌘+S`, `Ctrl/⌘+Shift+S` |
| Project info… | name, electrode, analyte, matrix, notes |
| Close project | `Ctrl/⌘+W`: asks about unsaved changes, then returns to the start page |
| Home (View menu, toolbar) | `Ctrl/⌘+Shift+H`: the start page, **without** closing anything; *Return to project* brings you back |
| Add data files… / Add data folder… | `Ctrl/⌘+Shift+O`, `Ctrl/⌘+Alt+O` |
| Export report (PDF)… / Export workbook (Excel)… | `Ctrl/⌘+E`, `Ctrl/⌘+Shift+E`: see below |
| Export all results (CSV)… | writes the CSV results of every analysis you have opened into a folder |

The title bar shows an unsaved-changes marker (●/\*). **Just looking does not count as a change** (opening a
run, switching tabs, display-only options); editing a setting does. Closing, opening or starting a new project
asks before discarding changes. A save writes a temporary file first and then renames it, so a crash or full
disk can never damage an existing project. A damaged or hostile project file is refused with a message and leaves
your current work untouched (nothing in a project is ever unpickled or run). There is no autosave: if the app
itself crashes, changes since the last save are lost.

*Export analysis…* in the CA tab still writes CSV / PNG / SVG for one run into `<data folder>/analysis/…`
(or beside the project when the data folder no longer exists).

## Reports and workbooks: everything in one file

Both are built from the analysis you see on screen (only runs you have opened are included), always in a light,
print-friendly style whatever theme the window uses.

* **PDF report** (`Export ▸ Report (PDF)`): a cover with the project info, a summary of results across runs, then
  one page per analysed run: **setup** (every parameter used), **results**, warnings, and the **plots**; a comparison page;
  an appendix with the step tables; page numbers in the footer.
* **Excel workbook** (`Export ▸ Workbook (Excel)`): a **Summary** sheet with a clickable contents list; for every run a
  *dashboard* sheet (setup, results, steps or cycles, and the plots) and a *data* sheet holding the **raw numbers** with an
  **editable native Excel chart**; plus a Compare sheet. The raw data is bit-for-bit what was loaded.

## Appearance

*View ▸ Appearance* (or the sun / moon button in the toolbar): **match system**, **light** or **dark**. The whole window and
every plot follow it, and the choice is remembered. A welcome page with your recent projects replaces the empty window until
data is loaded; rarely used settings sit in a folded **Advanced** section.

## Presets: reusable setups

A **preset** is a named protocol (stock concentration and unit, cell volume, volume per addition, response
method, settle time, windows, …) saved in your user config folder, so every project can use it
(`~/Library/Application Support/EchemAnalyzer/presets.json`; *Help ▸ Show presets folder*). The **Setup presets**
box exists in the CA tab and in the CV cycle tab: choose a name and **Apply** (to this run, or to all runs),
**Save as…**, **Delete**.

A preset never contains per-run data (step times, which points or cycles you excluded), and applying one never
touches them. If a run's volumes were still all at its default they follow the preset's volume; volumes you edited
individually are kept unless the preset carries a volume schedule. A CV preset with an unknown (0) scan rate does
not overwrite a scan rate the run already has. The file is plain JSON and validated on use: a damaged file is
moved aside as `presets.json.corrupt` instead of being overwritten.

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

## Versioning and releases

Versions follow [Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`); every release is an annotated git tag `vX.Y.Z`, and
[CHANGELOG.md](CHANGELOG.md) says what changed. The version lives in one place, `__version__` in `echem_analyzer/__init__.py`;
`python -m echem_analyzer --version` prints it and the Help ▸ About box, reports and projects record it.

To release:

1. Bump `__version__`; move the *Unreleased* notes in `CHANGELOG.md` under `## [X.Y.Z] - YYYY-MM-DD` (and add the link at the bottom).
2. Commit both: `git commit -am "chore(release): X.Y.Z"`.
3. `scripts/release.sh X.Y.Z --push`: pushing the tag also starts the installer build and the GitHub Release (see *Building the installers*). It checks you are on a clean `main`, that the version and the changelog agree and that the tag is new,
   runs the tests, creates the annotated tag with the changelog section as its message and pushes `main` and that tag.
   Without `--push` it only tags; the commit to publish is printed.

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/): `feat` (minor bump), `fix` (patch), `feat!` or a
`BREAKING CHANGE:` footer (major), and `docs`, `test`, `chore`, `build`, `refactor` for the rest.

## Building the installers

The installers are built with [PyInstaller](https://pyinstaller.org) by one script that is used both on your machine and in CI:

```sh
uv sync --group build
uv run python packaging/build.py                # freeze, smoke-test the frozen app, make this system's installer in dist/release/
uv run python packaging/build.py --no-installer # only freeze and smoke-test (about a minute)
```

PyInstaller cannot cross-compile, so each installer is built on its own system: that is what the GitHub Actions workflow does.
What the script produces: a `.dmg` on macOS (`hdiutil`), the Inno Setup installer and a portable zip on Windows (the installer needs
[Inno Setup](https://jrsoftware.org/isdl.php); without it only the zip is made), and a `.deb` on Linux (`dpkg-deb`).
The frozen app is smoke-tested before it is packaged: `--version`, then `--selftest`, which analyses a synthetic calibration, writes a PDF,
an Excel workbook and a project, reads the project back and draws every tab, all without a display
(`QT_QPA_PLATFORM=offscreen echem-analyzer --selftest`). A missing library or plugin therefore fails the build, not the user.

Files: `packaging/echem-analyzer.spec` (what goes into the bundle), `packaging/build.py` (the build), `packaging/windows/echem-analyzer.iss`
(the Windows installer), `assets/` (the icon; `uv run python packaging/make_icons.py` regenerates the PNG, ICO and ICNS from `icon.svg`).

### Releasing with GitHub Actions

`.github/workflows/ci.yml` runs the tests on every push and pull request. `.github/workflows/release.yml` builds and publishes:

1. Bump `__version__`, update `CHANGELOG.md`, commit (see *Versioning and releases*), then `scripts/release.sh X.Y.Z --push`.
2. The pushed tag `vX.Y.Z` starts the workflow. It checks that the tag equals the version and that the changelog has a section for it,
   runs the tests, then builds on four GitHub runners (macOS Apple silicon, macOS Intel, Windows, Ubuntu 22.04), smoke-tests every
   package (the `.deb` is installed with `apt`, the Windows installer is run silently, and the installed program's self-test must pass)
   and uploads the installers.
3. If all builds pass, the **release** job (the only one allowed to write to the repository) creates the GitHub Release with the changelog
   section as its notes, the installers and `SHA256SUMS.txt`.

To try the builds without releasing anything, open *Actions ▸ Release ▸ Run workflow*: the installers are kept as downloadable build
artifacts for 14 days and no release is made. Do this once before the first real tag.

Runner names change over time (`macos-15-intel`, `ubuntu-22.04`, …); if a job fails to start, check GitHub's list of hosted runners.
Dependabot (`.github/dependabot.yml`) proposes updates to the action versions.

**Signing.** macOS: enrol in the Apple Developer Program, create a *Developer ID Application* certificate, and add signing and
`notarytool` notarization steps to the macOS jobs (secrets: the certificate as base64 and its password, an app-specific password, the
team id). Windows: buy a code-signing certificate (or use Azure Trusted Signing) and sign the installer and `EchemAnalyzer.exe` with
`signtool` before packaging. Neither is set up here: the macOS app is only ad-hoc signed (the default of PyInstaller), the Windows
installer is unsigned.

## Tests

```sh
export UV_PROJECT_ENVIRONMENT="$HOME/.cache/echem-analyzer/venv"
uv run pytest                                       # I/O, maths, projects, presets, report / PDF / Excel, and the GUI driven offscreen
ECHEM_SHOT_DIR=/some/dir uv run pytest tests/test_gui_smoke.py tests/test_gui_project.py   # also saves screenshots
```

The suite also fails on any exception raised inside a Qt slot (PySide would otherwise print it and carry on), and the
plot / report / project / workbook code is tested to run without Qt.

Tests that need your real data use the sibling `Fabrication & Profilometry/2026-11-02 LPA GY CSF data` folder and are
skipped if it is missing. GUI tests work on temporary copies, with their settings and presets redirected to a temp
folder, so they never touch your data or your real configuration.
