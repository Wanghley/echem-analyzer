"""A self-describing data package of a project, for sharing, archiving or publishing (no Qt).

A ``.zip`` with everything another person needs to read the data without this program: the original instrument files (with
checksums), every curve as a tidy CSV in fixed units, the study's responses, the devices and acceptance rules, the analysis
settings, a readable snapshot of the results, a machine-readable description, and a README that says what is where and how the
numbers were obtained.  Nothing in it needs this app to open.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

from . import __version__
from . import acceptance as acc
from . import devices as dv
from .models import Curve, ProjectData
from .project import _json_default, collect_results
from .reporting import safe_name

RESPONSE_COLUMNS = ("id", "source_label", "technique", "analyzer", "quantity", "unit", "value", "sd", "conc", "conc_unit", "condition", "group",
                    "time", "temp_c", "role", "use", "note")
UNITS = {"current": "µA", "potential": "V", "time": "s", "impedance": "Ω", "frequency": "Hz"}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def curve_csv(c: Curve) -> str:
    """One curve as CSV: x and y with their units in the header, and any extra columns (an impedance spectrum's frequency, Z′, Z″)."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    extra = sorted(c.extra)
    w.writerow([f"x ({c.x_unit})" if c.x_unit else "x", f"y ({c.y_unit})" if c.y_unit else "y", *extra])
    for k in range(c.n):
        w.writerow([repr(float(c.x[k])), repr(float(c.y[k])), *[repr(float(c.extra[n][k])) if k < len(c.extra[n]) else "" for n in extra]])
    return buf.getvalue()


def responses_csv(data: ProjectData) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(RESPONSE_COLUMNS)
    for r in data.responses:
        d = r.to_dict()
        w.writerow(["" if d.get(c) is None else d.get(c) for c in RESPONSE_COLUMNS])
    return buf.getvalue()


def _readme(data: ProjectData, files: list[dict], n_curves: int) -> str:
    m = data.meta
    lines = [f"# {m.name or 'Echem Analyzer data package'}", "",
             f"Written by Echem Analyzer {__version__} on {datetime.now().strftime('%Y-%m-%d %H:%M')}.", ""]
    for label, value in (("Operator", m.operator), ("Electrode / sensor", m.electrode), ("Analyte", m.analyte), ("Sample matrix", m.matrix)):
        if value:
            lines.append(f"- {label}: {value}")
    if m.notes:
        lines += ["", m.notes]
    kept = sum(1 for f in files if f["raw"] == "included")
    lines += ["", "## What is here", "",
              f"- `raw/`: the original instrument files ({kept} of {len(files)} found; checksums in `metadata.json`).",
              f"- `curves/`: {n_curves} measurements, one CSV each, with the unit of every column in its header.",
              f"- `responses.csv`: the {len(data.responses)} numbers sent to the sensor study (peak currents, plateaus, ...), with concentration, "
              "electrode (`group`), condition, time, temperature and role.",
              "- `devices.csv`, `acceptance.json`: the electrodes' variants, lots and fabrication parameters, and the acceptance rules (when present).",
              "- `settings.json`: the analysis settings of every run and the study settings, so the analysis can be repeated.",
              "- `results/`: a readable snapshot of the computed results.",
              "- `metadata.json`: all of the above in machine-readable form.", "",
              "## Units", "", "Whatever the instrument wrote, the curves here are in " + ", ".join(f"{k} in {u}" for k, u in UNITS.items())
              + ". Impedance files keep frequency, Z′ and Z″ (Im Z negative for a capacitive spectrum) as extra columns.", "",
              "## How the numbers were obtained", "",
              "Calibrations are least-squares fits (linear, log-linear, Michaelis–Menten or Hill); sensitivity is the slope, the detection limit kσ/S "
              "with σ from blank replicates or the fit residuals, and unknowns are read from the fitted curve by inverse prediction with a confidence "
              "interval. Reproducibility is the RSD between electrodes at the same concentration, stability the retention of a response over time. "
              "Ion-selective electrodes are fitted as a Nernstian region and a plateau, with the IUPAC detection limit. Every setting used is in "
              "`settings.json`; the methods are described in full at https://github.com/Wanghley/echem-analyzer/blob/main/docs/analysis-methods.md."]
    return "\n".join(lines) + "\n"


def build_package(data: ProjectData, out_path: str | Path, include_raw: bool = True) -> dict:
    """Write the package and return what went in (counts and any raw file that could not be found)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    files_meta: list[dict] = []
    members: dict[str, bytes] = {}
    used: set[str] = set()

    def unique(name: str) -> str:
        base, k = name, 1
        while name in used:
            k += 1
            stem, dot, ext = base.rpartition(".")
            name = f"{stem}_{k}.{ext}" if dot else f"{base}_{k}"
        used.add(name)
        return name

    n_curves = 0
    for f in data.files:
        entry: dict = {"name": f.name, "format": f.fmt, "original_path": str(f.path), "curves": [], "raw": "not stored"}
        src = Path(f.path)
        if include_raw:
            if src.is_file():
                raw_name = unique(f"raw/{safe_name(src.name) or 'file'}")
                members[raw_name] = src.read_bytes()
                entry.update(raw="included", raw_member=raw_name, sha256=_sha256(src))
            else:
                entry["raw"] = "missing"
        for k, c in enumerate(f.curves):
            name = unique(f"curves/{safe_name(src.stem)}__{k:03d}_{c.kind}.csv")
            members[name] = curve_csv(c).encode("utf-8")
            entry["curves"].append({"member": name, "title": c.title, "kind": c.kind, "scan": c.scan, "points": c.n,
                                    "x_unit": c.x_unit, "y_unit": c.y_unit, "meta": {k2: v for k2, v in c.meta.items() if isinstance(v, (str, int, float, bool))}})
            n_curves += 1
        files_meta.append(entry)

    members["responses.csv"] = responses_csv(data).encode("utf-8")
    devices = dv.load(data.extras)
    if devices:
        members["devices.csv"] = dv.to_csv(devices).encode("utf-8")
    rules = acc.load(data.extras)
    if rules:
        members["acceptance.json"] = json.dumps([r.to_dict() for r in rules], indent=2, ensure_ascii=False).encode("utf-8")
    settings = {"ca": {k: v.to_dict() for k, v in data.ca_settings.items()}, "cv": {k: v.to_dict() for k, v in data.cv_settings.items()},
                "extras": data.extras}
    members["settings.json"] = json.dumps(settings, indent=2, ensure_ascii=False, default=_json_default).encode("utf-8")
    for name, text in collect_results(data).items():
        members[unique(name)] = text.encode("utf-8")
    meta = {"app": "Echem Analyzer", "version": __version__, "written": datetime.now().isoformat(timespec="seconds"), "units": UNITS,
            "project": {k: getattr(data.meta, k) for k in ("name", "operator", "electrode", "analyte", "matrix", "notes")},
            "files": files_meta, "responses": len(data.responses), "devices": len(devices), "acceptance_rules": len(rules)}
    members["metadata.json"] = json.dumps(meta, indent=2, ensure_ascii=False, default=_json_default).encode("utf-8")
    members["README.md"] = _readme(data, files_meta, n_curves).encode("utf-8")

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(members, key=lambda n: (n != "README.md", n != "metadata.json", n)):
            z.writestr(name, members[name])
    return {"path": str(out_path), "files": len(data.files), "curves": n_curves, "responses": len(data.responses),
            "raw_included": sum(e["raw"] == "included" for e in files_meta), "raw_missing": [e["name"] for e in files_meta if e["raw"] == "missing"]}
