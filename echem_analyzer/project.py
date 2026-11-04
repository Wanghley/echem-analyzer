"""Project files (no GUI imports).

A project is ONE self-contained ``.echemproj`` file (a zip archive) holding

* ``project.json``      metadata, the list of data files and curves, every analysis setting,
                        and the UI state, so the session can be restored;
* ``data/curves.npz``   the raw x/y arrays of every curve (embedded: the project reopens even
                        if the original CSV / pssession files have moved or are gone);
* ``results/*.csv``     a readable snapshot of the computed results (CA fits, CV cycle tables);
* ``plots/**/*.png``    the figures of every analysed run (the same ones the PDF report contains).
                        Results and plots are rewritten on every save and are *not* read back:
                        they are always recomputed from the data and settings.

Nothing is pickled, and only known archive members are ever read (nothing is extracted to disk).
"""
from __future__ import annotations

import copy
import dataclasses
import io
import json
import math
import os
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .ca_analysis import analyze_ca
from .cv_analysis import group_cv_runs
from .report import build_report
from .models import KIND_CA, CAAnalysisSettings, CVSettings, Curve, DataFile, ProjectData, ProjectMeta, Response  # noqa: F401
from .reporting import csv_text, cv_cycle_table, cv_summary_rows, safe_name, step_table

FORMAT = "echem-analyzer-project"
VERSION = 1
SUFFIX = ".echemproj"
MAX_MEMBER_BYTES = 512 * 1024 * 1024          # refuse absurd archive members (zip bombs)


class ProjectError(Exception):
    """A project file could not be read; the message is meant for the user."""


# --------------------------------------------------------------------------- helpers

def _json_default(o: Any):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def _read_member(z: zipfile.ZipFile, name: str) -> bytes:
    try:
        info = z.getinfo(name)
    except KeyError as exc:
        raise ProjectError(f"The project is missing '{name}'.") from exc
    if info.file_size > MAX_MEMBER_BYTES:
        raise ProjectError(f"'{name}' is implausibly large; refusing to read it.")
    return z.read(name)


# --------------------------------------------------------------------------- results snapshot

def collect_results(data: ProjectData) -> dict[str, str]:
    """Readable CSV snapshot of every analysis that has settings: ``{archive path: text}``."""
    out: dict[str, str] = {}
    errors: list[str] = []
    m = data.meta
    ident = [m.name, m.electrode, m.analyte, m.matrix]
    used: set[str] = set()

    def unique(base: str) -> str:
        name, k = base, 2
        while name in used:
            name, k = f"{base}_{k}", k + 1
        used.add(name)
        return name

    # --- chronoamperometry
    ca_header = [
        "project", "electrode", "analyte", "matrix", "file", "run", "method", "n_additions",
        "stock", "stock_unit", "V0_mL", "slope", "slope_se", "intercept", "R2", "sensitivity",
        "sensitivity_per_cm2", "sigma_uA", "LOD", "LOQ", "C_x", "C_x_se", "C_sample", "warnings",
        "C_x_CI95", "C_sample_CI95", "suitability", "sigma_source", "dilution_mode",
    ]
    ca_rows: list[list] = []
    for f in data.files:
        for c in f.curves:
            s = data.ca_settings.get(c.uid)
            if c.kind != KIND_CA or s is None:
                continue
            try:
                s2 = copy.deepcopy(s)
                r = analyze_ca(c.x, c.y, s2)
                fit = r.fit
                ca_rows.append(ident + [
                    f.name, c.label, s2.method, len(r.step_times), s2.stock_conc, s2.stock_unit, s2.v0_ml,
                    fit.slope if fit else "", fit.slope_se if fit else "", fit.intercept if fit else "",
                    fit.r2 if fit else "", r.sensitivity if fit else "", r.sensitivity_area if fit else "",
                    r.sigma if fit else "", r.lod if fit else "", r.loq if fit else "",
                    r.c_x if fit else "", r.c_x_se if fit else "", r.c_sample if fit else "",
                    " | ".join(r.warnings),
                    r.c_x_ci95 if fit else "", r.c_sample_ci95 if fit else "", r.suitability if fit else "",
                    r.sigma_used, s2.dilution_mode,
                ])
                header, rows = step_table(s2, r)
                out[f"results/ca/{unique(safe_name(f.stem + '__' + (c.meta.get('run_title') or c.title)))}__steps.csv"] = (
                    csv_text(header, rows))
            except Exception as exc:  # noqa: BLE001 - one bad run must not block saving the project
                errors.append(f"CA {f.name} / {c.label}: {exc}")
    if ca_rows:
        out["results/ca_summary.csv"] = csv_text(ca_header, ca_rows)

    # --- cyclic voltammetry
    cv_rows: list[list] = []
    for run in group_cv_runs(c for f in data.files for c in f.curves):
        s = data.cv_settings.get(run.uid)
        if s is None:
            continue
        try:
            header, rows = cv_cycle_table(run, s)
            out[f"results/cv/{unique(safe_name(Path(run.source).stem + '__' + run.title))}__cycles.csv"] = (
                csv_text(header, rows))
            for name, val, unit in cv_summary_rows(run, s):
                cv_rows.append(ident + [Path(run.source).name, run.title, name, val, unit])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"CV {Path(run.source).name} / {run.title}: {exc}")
    if cv_rows:
        out["results/cv_summary.csv"] = csv_text(
            ["project", "electrode", "analyte", "matrix", "file", "run", "quantity", "value", "unit"], cv_rows)
    # --- sensor study: every response, and the calibrations computed from them
    if data.responses:
        out["results/responses.csv"] = csv_text(
            ["id", "measurement", "technique", "analyzer", "quantity", "value", "sd", "unit", "conc", "conc_unit", "condition",
             "group", "time", "role", "used", "note"],
            [[r.id, r.source_label, r.technique, r.analyzer, r.quantity, r.value, "" if r.sd is None else r.sd, r.unit,
              "" if r.conc is None else r.conc, r.conc_unit, r.condition, r.group, "" if r.time is None else r.time,
              r.role, int(r.use), r.note] for r in data.responses])
        try:
            from .study_report import compute_study
            study = compute_study(list(data.responses), data.extras)
            if study.calibrations:
                out["results/study_calibrations.csv"] = csv_text(
                    ["quantity", "condition", "model", "R2", "sensitivity", "sensitivity_unit", "LOD", "LOQ", "conc_unit",
                     "range_low", "range_high", "n_points"],
                    [[cs.options.quantity, cs.options.condition, cs.model, cs.fit.r2, cs.sensitivity, cs.sensitivity_unit, cs.lod,
                      cs.loq, cs.conc_unit, cs.fom.range_low if cs.fom else "", cs.fom.range_high if cs.fom else "", len(cs.x)]
                     for cs in study.calibrations])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"sensor study: {exc}")
    if errors:
        out["results/_errors.txt"] = "\n".join(errors) + "\n"
    return out


# --------------------------------------------------------------------------- save

def save_project(path: str | Path, data: ProjectData, include_plots: bool = True) -> ProjectMeta:
    """Write ``data`` to ``path`` atomically and return the metadata with updated timestamps.

    With ``include_plots`` the figures of every analysed run are stored inside the archive too.
    """
    path = Path(path)
    now = datetime.now().isoformat(timespec="seconds")
    meta = copy.copy(data.meta)
    meta.modified = now
    meta.created = meta.created or now
    data = dataclasses.replace(data, meta=meta)

    arrays: dict[str, np.ndarray] = {}
    files_json = []
    n = 0
    for f in data.files:
        curves = []
        for c in f.curves:
            arrays[f"x{n}"] = np.asarray(c.x, dtype=float)
            arrays[f"y{n}"] = np.asarray(c.y, dtype=float)
            for name, arr in c.extra.items():
                arrays[f"e{n}__{name}"] = np.asarray(arr, dtype=float)
            curves.append({
                "uid": c.uid, "kind": c.kind, "title": c.title, "x_unit": c.x_unit, "y_unit": c.y_unit,
                "scan": c.scan, "meta": c.meta, "source": c.source, "array": n, "extra": sorted(c.extra),
            })
            n += 1
        files_json.append({"path": str(f.path), "fmt": f.fmt, "exported_at": f.exported_at, "curves": curves})

    doc = {
        "format": FORMAT, "version": VERSION, "app_version": data.app_version,
        "meta": meta.to_dict(), "files": files_json,
        "ca_settings": {uid: s.to_dict() for uid, s in data.ca_settings.items()},
        "cv_settings": {uid: s.to_dict() for uid, s in data.cv_settings.items()},
        "ui": data.ui,
        "responses": [r.to_dict() for r in data.responses],
        "extras": data.extras,
    }
    npz = io.BytesIO()
    np.savez_compressed(npz, **arrays)
    results = collect_results(data)
    plot_files = ({f"plots/{rel}": fig.png for rel, fig in build_report(data).figures().items()}
                  if include_plots else {})
    doc["plots"] = sorted(plot_files)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("project.json", json.dumps(doc, indent=2, default=_json_default))
            z.writestr("data/curves.npz", npz.getvalue())
            for rel, text in results.items():
                z.writestr(rel, text)
            for rel, png in plot_files.items():
                z.writestr(rel, png)
        os.replace(tmp, path)          # a crash mid-save can never corrupt an existing project
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return meta


# --------------------------------------------------------------------------- load

def load_project(path: str | Path) -> ProjectData:
    path = Path(path)
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ProjectError(f"{path.name} is not a readable project file ({exc}).") from exc
    with z:
        try:
            doc = json.loads(_read_member(z, "project.json").decode("utf-8"))
        except ValueError as exc:
            raise ProjectError(f"{path.name}: project.json is damaged ({exc}).") from exc
        if not isinstance(doc, dict) or doc.get("format") != FORMAT:
            raise ProjectError(f"{path.name} is not an Echem Analyzer project.")
        version = doc.get("version")
        if not isinstance(version, int) or version > VERSION:
            raise ProjectError(
                f"{path.name} was saved by a newer version of Echem Analyzer (format {version}); please update.")

        try:
            npz = np.load(io.BytesIO(_read_member(z, "data/curves.npz")), allow_pickle=False)
        except ValueError as exc:
            raise ProjectError(f"{path.name}: the embedded data is damaged ({exc}).") from exc

        try:
            files = []
            for fj in doc.get("files", []):
                curves = []
                for cj in fj["curves"]:
                    k = int(cj["array"])
                    x, y = np.asarray(npz[f"x{k}"], float), np.asarray(npz[f"y{k}"], float)
                    if x.ndim != 1 or x.shape != y.shape:
                        raise ProjectError(f"{path.name}: curve '{cj.get('title')}' has inconsistent data.")
                    extra = {name: np.asarray(npz[f"e{k}__{name}"], float) for name in cj.get("extra", [])}
                    if any(a.shape != x.shape for a in extra.values()):
                        raise ProjectError(f"{path.name}: curve '{cj.get('title')}' has inconsistent extra columns.")
                    curves.append(Curve(
                        kind=cj["kind"], title=cj["title"], x=x, y=y, x_unit=cj.get("x_unit", ""),
                        y_unit=cj.get("y_unit", "µA"), scan=cj.get("scan"), meta=cj.get("meta") or {},
                        source=cj.get("source", fj["path"]), uid=cj["uid"], extra=extra,
                    ))
                files.append(DataFile(path=Path(fj["path"]), fmt=fj.get("fmt", "csv"), curves=curves,
                                      exported_at=fj.get("exported_at", "")))
            ca = {uid: CAAnalysisSettings.from_dict(d) for uid, d in (doc.get("ca_settings") or {}).items()}
            cv = {uid: CVSettings.from_dict(d) for uid, d in (doc.get("cv_settings") or {}).items()}
            meta = ProjectMeta.from_dict(doc.get("meta") or {})
            responses = [Response.from_dict(d) for d in (doc.get("responses") or [])]
            extras = doc.get("extras") or {}
            if not isinstance(extras, dict):
                raise ValueError("extras is not an object")
        except (KeyError, TypeError, ValueError) as exc:
            raise ProjectError(f"{path.name}: the project content is damaged ({exc!r}).") from exc

    return ProjectData(meta=meta, files=files, ca_settings=ca, cv_settings=cv,
                       ui=doc.get("ui") or {}, app_version=str(doc.get("app_version", "")),
                       responses=responses, extras=extras)
