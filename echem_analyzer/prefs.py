"""User preferences (no Qt): plot style, palettes, export and analysis defaults.

Stored as plain JSON in the user's config folder next to the presets.  Every value is validated on read and write, so a
hand-edited or damaged file can never break the app: unknown keys are dropped, bad values fall back to the default and a
file that is not JSON is moved aside as ``preferences.json.corrupt``.

Plots, reports and the windows all read the same object, so a preference applies everywhere at once.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .presets import default_store_path


@dataclass(frozen=True)
class Pref:
    key: str
    default: Any
    kind: str                                   # choice | float | int | bool | text
    choices: tuple[Any, ...] = ()
    lo: float = 0.0
    hi: float = 0.0


SCHEMA: dict[str, Pref] = {p.key: p for p in (
    Pref("plot_style", "modern", "choice", ("modern", "publication")),
    Pref("plot_palette", "default", "choice", ("default", "colorblind", "greyscale")),
    Pref("plot_font_scale", 1.0, "float", lo=0.8, hi=1.6),
    Pref("export_dpi", 200, "choice", (150, 200, 300, 600)),
    Pref("page_size", "A4", "choice", ("A4", "Letter")),
    Pref("operator", "", "text"),
    Pref("institution", "", "text"),
    Pref("lod_k", 3.0, "float", lo=1.0, hi=10.0),
    Pref("conc_unit", "µM", "choice", ("M", "mM", "µM", "nM", "pM", "fM")),
    Pref("recent_limit", 5, "int", lo=1, hi=12),
)}
DEFAULTS: dict[str, Any] = {k: p.default for k, p in SCHEMA.items()}


def clean(key: str, value: Any) -> Any:
    """``value`` if it is valid for ``key``, else the default."""
    p = SCHEMA[key]
    if p.kind == "choice":
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return value if value in p.choices else p.default
    if p.kind in ("float", "int"):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return p.default
        v = min(max(float(value), p.lo), p.hi)
        return int(round(v)) if p.kind == "int" else v
    if p.kind == "bool":
        return value if isinstance(value, bool) else p.default
    return value.strip()[:120] if isinstance(value, str) else p.default


class Prefs:
    def __init__(self, path: Optional[Path] = None):
        self.path: Path = path or default_store_path().with_name("preferences.json")
        self._values: dict[str, Any] = dict(DEFAULTS)
        self._listeners: list[Callable[[], None]] = []
        self.loaded_from_disk = False
        self.load()

    # ------------------------------------------------------------------ access
    def get(self, key: str) -> Any:
        return self._values[key]

    def set(self, key: str, value: Any, save: bool = True) -> None:
        v = clean(key, value)
        if v == self._values[key]:
            return
        self._values[key] = v
        if save:
            self.save()
        self._notify()

    def update(self, values: dict[str, Any]) -> None:
        changed = False
        for k, v in values.items():
            if k in SCHEMA and clean(k, v) != self._values[k]:
                self._values[k] = clean(k, v)
                changed = True
        if changed:
            self.save()
            self._notify()

    def reset(self) -> None:
        self._values = dict(DEFAULTS)
        self.save()
        self._notify()

    def as_dict(self) -> dict[str, Any]:
        return dict(self._values)

    def is_default(self) -> bool:
        return self._values == DEFAULTS

    def signature(self) -> str:
        """Everything that changes how a figure looks, as one string (a cache key for rendered figures)."""
        return "|".join(str(self._values[k]) for k in ("plot_style", "plot_palette", "plot_font_scale", "export_dpi"))

    def on_change(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def _notify(self) -> None:
        for fn in list(self._listeners):
            fn()

    # ------------------------------------------------------------------ storage
    def use_path(self, path: Path) -> None:
        """Point at another file (tests, portable installs) and reload."""
        self.path = Path(path)
        self.load()

    def load(self) -> None:
        self._values = dict(DEFAULTS)
        self.loaded_from_disk = False
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return
        try:
            doc = json.loads(raw)
            if not isinstance(doc, dict):
                raise ValueError("not an object")
        except ValueError:
            try:
                os.replace(self.path, self.path.with_name(self.path.name + ".corrupt"))
            except OSError:
                pass
            return
        for k, v in doc.items():
            if k in SCHEMA:
                self._values[k] = clean(k, v)
        self.loaded_from_disk = True

    def save(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name + ".", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._values, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False                                          # read-only config folder: keep working in memory


prefs = Prefs()
