"""Named analysis presets ("setups") shared by all projects (no GUI imports).

A preset holds the *protocol* of an analysis (stock concentration, cell volume, windows,
method, ...) but never the per-run data (step times, which points were excluded).  They
live in one JSON file in the user's config folder.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .ca_analysis import sync_step_lists
from .models import CAAnalysisSettings, CVSettings

STORE_VERSION = 1
KIND_CA, KIND_CV = "ca", "cv"

# Protocol fields only: step_times / ignored_events / per-step overrides and inclusion flags
# describe one particular run, so they are not part of a preset.
CA_FIELDS = (
    "drop_last", "detect_k", "detect_min_sep", "detect_lag", "expected_steps",
    "method", "settle", "window", "end_guard",
    "stock_conc", "stock_unit", "v0_ml", "v_add_ul", "verified_ul", "dilution_mode",
    "v_sample_ml", "background", "area_cm2", "v0_unit", "v_add_unit", "verified_unit", "v_sample_unit", "bkg_unit",
    "sigma_source", "baseline_window", "blank_replicates", "lod_k", "loq_k", "suitability_min", "plausible_max",
    "include_baseline", "compare_baseline",
)
CV_FIELDS = ("scan_rate", "anodic_window", "cathodic_window", "baseline", "stability_tol")

# fields whose value must be one of a fixed set (a hand-edited preset file may contain anything)
ALLOWED = {
    "method": {"plateau", "drift"},
    "sigma_source": {"baseline", "fit", "blank"},
    "dilution_mode": {"concentration", "signal", "none"},
    "stock_unit": {"mM", "µM", "nM", "M", "pM"},
    "v0_unit": {"mL", "µL"}, "v_add_unit": {"mL", "µL"}, "verified_unit": {"mL", "µL"}, "v_sample_unit": {"mL", "µL"},
    "bkg_unit": {"nA", "µA", "mA"},
    "baseline": {"none", "linear"},
}


def default_store_path() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "EchemAnalyzer"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home())) / "EchemAnalyzer"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "echem-analyzer"
    return base / "presets.json"


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a temp file in the same folder, then rename: a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- value handling

def _coerce(default: Any, value: Any) -> Any:
    """Coerce ``value`` to the type of the dataclass default; raise ValueError if impossible."""
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        raise ValueError("expected a boolean")
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("expected a number")
        return int(value)
    if isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("expected a number")
        return float(value)
    if isinstance(default, str):
        if not isinstance(value, str):
            raise ValueError("expected text")
        return value
    if isinstance(default, list):                         # a list of numbers (blank replicate currents)
        if (isinstance(value, (list, tuple)) and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
            return [float(v) for v in value]
        raise ValueError("expected a list of numbers")
    if default is None:                                   # Optional[[lo, hi]] windows
        if value is None:
            return None
        if (isinstance(value, (list, tuple)) and len(value) == 2
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
            return [float(value[0]), float(value[1])]
        raise ValueError("expected [low, high] or null")
    raise ValueError("unsupported field")


def _defaults(cls) -> dict[str, Any]:
    return {f.name: getattr(cls(), f.name) for f in fields(cls)}


def ca_preset_from_settings(s: CAAnalysisSettings) -> dict[str, Any]:
    out = {k: getattr(s, k) for k in CA_FIELDS}
    vols = [float(v) for v in s.step_volumes_ul]
    if vols and any(abs(v - s.v_add_ul) > 1e-12 for v in vols):   # keep only a real volume schedule
        out["step_volumes_ul"] = vols
    return out


def apply_ca_preset(s: CAAnalysisSettings, preset: dict[str, Any]) -> list[str]:
    """Apply a preset to ``s`` in place; returns the names of fields that were ignored as invalid."""
    defaults = _defaults(CAAnalysisSettings)
    ignored = []
    sync_step_lists(s)
    if "dilution_mode" not in preset and ("dilution_conc" in preset or "dilution_signal" in preset):
        preset = dict(preset)                              # a preset saved before the dilution modes existed
        preset["dilution_mode"] = ("concentration" if preset.get("dilution_conc", True)
                                   else ("signal" if preset.get("dilution_signal", False) else "none"))
    # Volumes that were all still at the run's default were never customised, so they follow a
    # new default; individually edited volumes are kept (unless the preset brings a schedule).
    was_uniform = all(abs(v - s.v_add_ul) < 1e-12 for v in s.step_volumes_ul)
    for k in CA_FIELDS:
        if k in preset:
            try:
                v = _coerce(defaults[k], preset[k])
                if k in ALLOWED and v not in ALLOWED[k]:
                    raise ValueError("not an allowed value")
                setattr(s, k, v)
            except ValueError:
                ignored.append(k)
    if was_uniform:
        s.step_volumes_ul = [s.v_add_ul] * len(s.step_times)
    vols = preset.get("step_volumes_ul")
    if isinstance(vols, list):
        try:
            nums = [float(v) for v in vols]
        except (TypeError, ValueError):
            ignored.append("step_volumes_ul")
        else:
            for j in range(len(s.step_times)):
                s.step_volumes_ul[j] = nums[j] if j < len(nums) else s.v_add_ul
    return ignored


def cv_preset_from_settings(s: CVSettings) -> dict[str, Any]:
    return {k: getattr(s, k) for k in CV_FIELDS}


def apply_cv_preset(s: CVSettings, preset: dict[str, Any]) -> list[str]:
    defaults = _defaults(CVSettings)
    ignored = []
    for k in CV_FIELDS:
        if k in preset:
            try:
                v = _coerce(defaults[k], preset[k])
                if k in ALLOWED and v not in ALLOWED[k]:
                    raise ValueError("not an allowed value")
                if k == "scan_rate":
                    if v < 0:
                        raise ValueError("negative scan rate")
                    if v == 0:          # 0 = unknown (e.g. saved from a CSV): keep what the run knows
                        continue
                setattr(s, k, v)
            except ValueError:
                ignored.append(k)
    return ignored


# --------------------------------------------------------------------------- store

class PresetStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else default_store_path()
        self.last_error: str = ""
        self._data = self._read()

    def _read(self) -> dict[str, Any]:
        empty = {"version": STORE_VERSION, KIND_CA: {}, KIND_CV: {}}
        if not self.path.is_file():
            return empty
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("top level is not an object")
            for kind in (KIND_CA, KIND_CV):
                if not isinstance(raw.get(kind, {}), dict):
                    raise ValueError(f"'{kind}' is not an object")
                raw.setdefault(kind, {})
            raw.setdefault("version", STORE_VERSION)
            return raw
        except (OSError, ValueError) as exc:
            # keep the unreadable file for the user instead of silently overwriting it later
            self.last_error = f"Preset file {self.path.name} was unreadable ({exc}); started empty."
            try:
                os.replace(self.path, self.path.with_name(self.path.name + ".corrupt"))
            except OSError:
                pass
            return empty

    def names(self, kind: str) -> list[str]:
        return sorted(self._data[kind], key=str.lower)

    def get(self, kind: str, name: str) -> Optional[dict[str, Any]]:
        entry = self._data[kind].get(name)
        return dict(entry.get("values", {})) if isinstance(entry, dict) else None

    def save(self, kind: str, name: str, values: dict[str, Any]) -> None:
        name = name.strip()
        if not name:
            raise ValueError("A preset needs a name.")
        self._data[kind][name] = {"saved": datetime.now().isoformat(timespec="seconds"), "values": values}
        self._write()

    def delete(self, kind: str, name: str) -> bool:
        if name in self._data[kind]:
            del self._data[kind][name]
            self._write()
            return True
        return False

    def _write(self) -> None:
        atomic_write_text(self.path, json.dumps(self._data, indent=2, ensure_ascii=False))
