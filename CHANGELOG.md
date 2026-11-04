# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`, tagged `vMAJOR.MINOR.PATCH`.

## [Unreleased]

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

[Unreleased]: https://github.com/Wanghley/echem-analyzer/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/Wanghley/echem-analyzer/releases/tag/v0.4.0
