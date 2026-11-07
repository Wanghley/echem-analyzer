# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`, tagged `vMAJOR.MINOR.PATCH`.

## [Unreleased]

## [0.5.1] - 2026-11-06

A quieter start page, an About window with self-updating, and a new README.

### Added
- **Updates:** the About window has an **Updates** card (and *Help ▸ Check for updates…* starts it): it finds the newest GitHub release, shows
  its notes, downloads the installer for this system, verifies it against `SHA256SUMS.txt`, and installs it and reopens the app. Unsaved work is
  asked about first; if the install fails the old version is started again. Installed macOS apps, the Windows installer version and the
  Debian/Ubuntu package update themselves; the Windows portable zip and a run from source are pointed to the release page. A toolbar badge and a
  notice on the start page announce an update. The app looks once a day at startup (switch off in *Settings ▸ Files and storage*), contacts
  github.com only and sends nothing but its version.
- **About window** (*Help ▸ About*): a banner, version information (app, installation type, system, Python, PySide6, Qt, location) with a
  *Copy details* button, the author with links (website, GitHub, LinkedIn, Instagram), a support section and the methods text (folded away).
- **Buy me a coffee**: a button that is always in the toolbar. It opens a small card with the link, which also slides in once at startup.
  *Maybe later* closes it; *Don't show this at startup* (or *Settings ▸ Files and storage*) turns the startup card off; the button stays.
- **Signature:** *Made with 💙 by Wanghley Soares Martins* in the start-page footer and the About window.
- **File ▸ Open recent** is kept up to date, follows the *recent projects shown* setting and has a *Clear menu* entry.
- The package metadata names the author and carries the project, homepage and funding links.
- Documentation: a new README (screenshots, install, quick start) and `docs/` with the user guide, the analysis methods and the development guide.

### Changed
- **The start page is now minimal**: one centred column (name, version, update notice, three ways in with their shortcuts, a row of topics,
  help links) and a footer that stays in view. Recent projects moved to *File ▸ Open recent*.
- **The left panel is the project**: it is titled *Project* and shows the project's name, an unsaved marker, where it is saved and its files; the
  name and path moved there from the toolbar.
- Form controls (drop-downs, spin boxes, sliders, progress bars) now follow the theme, with crisp arrows; primary buttons have a gentle gradient
  and a disabled state; the toolbar separators are light lines instead of black ones.

### Fixed
- The folded-section headers (*Advanced…*) are left-aligned, as intended, instead of centred.
- The status bar says "1 file" and "1 CV run" instead of "1 files" and "1 CV runs".
- `SHA256SUMS.txt` is written with Unix line endings. The Windows runner's CRLF made `shasum -a 256 -c SHA256SUMS.txt` fail for the two Windows
  files of 0.5.0 on macOS and Linux (the files themselves were intact).

## [0.5.0] - 2026-11-04

A more rigorous standard-addition analysis, and the first installers for macOS, Windows and Debian/Ubuntu.

### Added
- **Installers** for macOS (`.dmg`, Apple silicon and Intel), Windows (installer and portable zip) and Debian/Ubuntu (`.deb`), built with
  PyInstaller by `packaging/build.py` and, on a version tag, by GitHub Actions, which publishes them as a GitHub Release with checksums.
  Every build smoke-tests the frozen program, and the `.deb` and the Windows installer are installed and run before they are released.
- `python -m echem_analyzer --selftest [FILE]`: analyses, reports, saves and draws every tab without a display, and writes the outcome as JSON.
- Double-clicking a `.echemproj` opens it in the app (macOS, Windows installer and Linux package register the file type); the app has a window icon.
- A CI workflow that runs the tests on every push and pull request.
- **Standard addition:** unit selectors for V₀, sample volume, volume per addition, verified (pipette-checked) volume and background current;
  C_x with a 95 % confidence interval and C_sample with its interval; the suitability check b·√Sxx/s(y); σ for LOD and LOQ from the residual
  s(y/x), the baseline noise or the SD of blank replicates (all three always shown); the result with and without the baseline point;
  warnings for a zero background, an impossible sample volume, a missing or double dilution correction, an implausible result, patterned
  residuals, a failed suitability check, a baseline-noise LOD far below the s(y/x) LOD, a point count that does not match the additions, and a
  settle time + window longer than a segment. All new settings are saved in presets and projects.
- `echem_analyzer.standard_addition.evaluate_points`: the same calculation for any (added concentration, signal) arrays, used by the CA analysis.

### Changed
- **Dilution is now one choice** (concentration, signal scaling or none) instead of two switches that could both be on and correct the dilution
  twice. Older projects and presets with both on keep the concentration correction.
- **LOD = 3.3σ/|b|** (was 3σ/|b|; the factor is adjustable) and the default σ is the residual s(y/x) (was the baseline noise). On data whose
  plateaus scatter more than the trace noise this gives a much larger, more honest LOD, and a warning says so.

### Known limits
- The installers are not signed: macOS Gatekeeper and Windows SmartScreen show a warning on first launch (the README explains how to
  continue). The macOS app is only ad-hoc signed and not notarized.
- The DPV, SWV, LSV, EIS, OCP and chronocoulometry readers still have only been checked on synthetic data, not on real instrument files.

## [0.4.0] - 2026-11-04

First tagged release: the platform for sensor electrochemistry beyond standard addition.

### Added
- **Techniques:** DPV, SWV, NPV, LSV, EIS, OCP, chronopotentiometry and chronocoulometry next to chronoamperometry and CV; technique
  recognition from the PalmSens method id or the title, with a manual override; import of any x–y table (*Import table…*).
- **Analyses:** baseline-corrected peaks, equivalent-circuit EIS fits (Randles with CPE and Warburg), step response and steady state,
  Cottrell and Anson.
- **Sensor study:** responses table, calibration (linear, log-linear, Michaelis–Menten, Hill; AIC), LOD, LOQ and linear range, reproducibility,
  stability, selectivity and spike recovery.
- **Electrode characterisation:** Randles–Sevcik electroactive area, scan-rate exponent, Laviron kinetics, double-layer capacitance.
- **Output:** PDF report and Excel workbook (native editable charts) that include the study; `.echemproj` projects with data, settings,
  responses and plots in one file.
- **Interface:** light and dark themes, Home and Close project, resizable panels that remember their sizes, a searchable in-app guide,
  a settings window (publication plot style, colour-blind-safe and greyscale palettes, export resolution, page size, defaults),
  `python -m echem_analyzer --version`.

### Known limits
- The DPV, SWV, LSV, EIS, OCP and chronocoulometry readers, the PalmSens method ids and the EIS layout inside `.pssession` were built from
  the file format and checked on synthetic data only; use the technique override or *Import table…* if a file is misread.
- Standard-addition numbers need your real cell volume, addition volumes and stock concentration (the defaults are placeholders).

## Earlier versions (before this repository)

- 0.3: plots embedded in projects, PDF and Excel export, redesigned light and dark interface.
- 0.2: multi-cycle CV analysis, saveable projects and named presets.
- 0.1: chronoamperometry standard addition with LOD and LOQ, and comparison of runs.

[Unreleased]: https://github.com/Wanghley/echem-analyzer/compare/v0.5.1...HEAD
[0.5.1]: https://github.com/Wanghley/echem-analyzer/releases/tag/v0.5.1
[0.5.0]: https://github.com/Wanghley/echem-analyzer/releases/tag/v0.5.0
[0.4.0]: https://github.com/Wanghley/echem-analyzer/releases/tag/v0.4.0
