# User guide

How to work with Echem Analyzer. The methods behind the numbers are in [analysis-methods.md](analysis-methods.md); the in-app **Guide** (`F1`) holds the same material, a glossary and troubleshooting.

## Workflow: any measurement, not only standard addition

Besides CA standard addition and multi-cycle CV the app handles **DPV / SWV / NPV / LSV, EIS, OCP / CP, chronocoulometry**
and any x–y table (*Measurements ▸ Import table…*). Workflow: **Measurements** (technique recognised from the method id or
title; correct it in the table if wrong) → **Analysis** (peak height, equivalent-circuit fit, step response, steady state,
Cottrell/Anson) → *Add to study* → **Sensor study** (Responses table; calibration with model comparison, LOD/LOQ and linear
range; reproducibility between electrodes; stability over time; selectivity; spike recovery). **Electrode** does scan-rate
(Randles–Sevcik ECSA, Laviron) and Cdl studies from CV series. PDF, Excel and project snapshots include all of it.

**Instrument files.** PalmSens `.csv` / `.pssession`, Gamry `.DTA`, CH Instruments `.txt`, BioLogic `.mpt` and Autolab Nova
exports are recognised by what they contain (a `.txt` can come from several instruments), not only by their suffix. A table that
no reader recognises opens the import dialog when you drop it on the window. CV files that hold all cycles in one trace are cut
into scans at their reversal points; when a file does not state the scan rate, one is estimated from the time and potential
columns and marked as estimated.

**Wearable sensing.** The step-response analysis gives t90 and t95, can treat steps against the first one as *recoveries* (their own times), and can
remove a straight-line baseline drift. Fill the **T (°C)** column of the Responses table for standards measured at several temperatures and the
**Temperature** tab (the last Study tab) gives the temperature coefficient in % per °C with its uncertainty, the response at a reference temperature, and
what compensating for it does to the scatter.

**Ion-selective electrodes.** Tick *Ion-selective electrode (potentiometric)* in the Calibration panel and give the ion's charge, the temperature and
(optionally) the background ionic strength. The app finds the Nernstian region, reports the slope as a percentage of the theoretical one, the IUPAC
detection limit, the region's limits and E°, and describes each unknown as below the limit, in the curved foot, inside the region or above it.

**Response as the change from the blank.** For label-free and signal-off sensors, whose electrodes each start from a different baseline, choose
*Response as ▸ Change from the blank* (or *Percent change*) in the Calibration panel; measure a blank (role *blank*) on each electrode.

**Calibration checks.** Every sample read from a calibration gets a 95 % interval as well as ± SE. For straight-line calibrations the study also
tests each standard: ones that sit far from the line, or that move it a lot, are ringed on the plot, named in the warnings and listed under
*Standards to check*. Nothing is removed for you.

**Bench calculators** (*Tools ▸ Bench calculators…*, `Ctrl/⌘ + Shift + B`): dilutions (C₁V₁ = C₂V₂ with any one unknown), a serial dilution,
the stock volumes of a standard-addition series (the exact inverse of the analysis's own model, with warnings for volumes below a pipette's
range and for a diluted matrix, and rounding to your pipette), the peak current to expect from a redox couple with the instrument range to set
for it, the Nernst slope and its temperature dependence, ionic strength with the Debye length, and the mass to weigh out. Pages recompute as
you type and say what is missing instead of showing a wrong number.

**Import checks.** Each curve is looked over when it is loaded. The **Checks** column of *Measurements* says *OK* or how many errors
and warnings it has (hover for the details; select the curve to read them beside the preview), and the report's Overview lists them.
They flag points that are not numbers, time that runs backwards, a current stuck at the instrument's limit, isolated spikes, a flat line,
an open-circuit potential that has not settled, units that cannot be right, impossible impedance frequencies and the same run loaded twice.
They say *look at this*; nothing is changed or removed for you.

**Not verified on real files:** the DPV/SWV/LSV/EIS/OCP/CC parsers and PalmSens method ids were built from the format and
tested on synthetic data with known answers only; the EIS layout of `.pssession` is a best guess. The Gamry, CH Instruments,
BioLogic and Autolab readers are **preview**: written from the manufacturers' file descriptions and tested on synthetic files.
Use the technique override and *Import table…* if a file is misread, and check the first real result by hand.

## Resizable panels

Every panel border has a visible grip: drag the one between a plot and its settings panel (right) or its table (below), or the edge of the
Project panel (left), to make it larger or smaller. **Double-click** a grip to reset that panel; *View ▸ Reset panel sizes* resets them all.
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
every plot follow it, and the choice is remembered. Rarely used settings sit in a folded **Advanced** section.

The **start page** (shown until data is loaded, and any time through *Home*) is one quiet, centred column: the name and version (with an
update notice when there is one), three ways in (open a project, add files, add a folder, each with its keyboard shortcut), a row of topics
that open the matching chapter of the guide, and links to the guide, shortcuts, settings and About. A footer with the signature and links stays
in view. **Recent projects** are under *File ▸ Open recent* (as many as *Settings ▸ Files and storage* allows; *Clear menu* empties the list),
and an open project shows in the **Project** panel on the left: its name, an unsaved marker, where it is saved, and the files it holds.

The start page footer (**Made with ♥ by Wanghley Soares Martins**) and the **About** window (*Help ▸ About Echem Analyzer*) carry the author's links, and the toolbar
always has a **Buy me a coffee** button; it opens a small card with the link. The card also slides in once when the app starts;
close it with *Maybe later*, or choose *Don't show this at startup* (the same switch is in *Settings ▸ Files and storage*). The card never
blocks the window and the button stays either way.

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

## Updates

*Help ▸ Check for updates…* (or the **Updates** card in *Help ▸ About*) looks for a newer release on GitHub. When there is one the toolbar and
the start page show an **Update available** notice. **Download and install** fetches the installer for your system, checks it against the
release's `SHA256SUMS.txt`, and then **Install and restart** closes the app, installs the update and opens it again; if your project has
unsaved changes you are asked first, so nothing is lost. If anything goes wrong during the install, the old version is started again.

* **Which copies update themselves:** the macOS app in a folder you can write to (normally *Applications*), the Windows installer version
  (it upgrades in place, for the same user or all users as installed), and the Debian/Ubuntu package (it asks for your password through
  `pkexec`; without it, the package is handed to your system's installer). The Windows **portable** zip and a run **from source** show
  the release page instead.
* **Privacy:** the app contacts `github.com` only, at most once a day at startup, and sends nothing but its version number. Turn the startup
  check off in *Settings ▸ Files and storage*; a manual check always works.
* **Safety:** downloads are only accepted from this project's GitHub releases over HTTPS (also after redirects), are capped in size, and are
  deleted if they do not match the published checksum. The checksum protects against a damaged download, not against a release that was
  built by someone else, because the installers are not code-signed (see [Install](../README.md#-install)).
