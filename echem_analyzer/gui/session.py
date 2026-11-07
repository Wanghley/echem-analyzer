"""Shared application state: loaded files and the per-run analysis settings."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from PySide6.QtCore import QObject, QSettings, Signal

from .. import __version__, io_palmsens
from ..ca_analysis import detect_step_events, sync_step_lists
from ..cv_analysis import CVRun, group_cv_runs
from ..analyzers import ANALYZERS, ResponseSpec, upsert_responses
from ..models import KIND_CA, KIND_CV, CAAnalysisSettings, CVSettings, Curve, DataFile, Response
from ..labels import curve_run_label, curve_short_label, file_short, run_label  # noqa: F401 (re-exported)
from ..presets import PresetStore
from ..prefs import prefs
from ..project import ProjectData, ProjectMeta, load_project, save_project
from ..reporting import analysis_dir_for, load_saved_settings

MAX_RECENT = 12

# Fields remembered between runs / sessions and used to seed new analyses.
DEFAULT_FIELDS = (
    ("stock_conc", float), ("stock_unit", str), ("v0_ml", float), ("v_add_ul", float),
    ("v_sample_ml", float), ("area_cm2", float), ("dilution_mode", str), ("v0_unit", str), ("v_add_unit", str),
    ("v_sample_unit", str), ("bkg_unit", str), ("verified_unit", str),
)


class Session(QObject):
    files_changed = Signal()
    analysis_changed = Signal(str)       # uid of the CA curve (or CV run) whose settings changed
    project_changed = Signal()           # project name / path / unsaved state changed
    responses_changed = Signal()         # the sensor study's table changed

    def __init__(self, qsettings: Optional[QSettings] = None, presets_path: Optional[Path] = None):
        super().__init__()
        self.qs = qsettings if qsettings is not None else QSettings("EchemAnalyzer", "EchemAnalyzer")
        # A caller-supplied QSettings (tests) keeps its presets beside its own file, so such a
        # session can never touch the real presets in the user's config folder.
        if presets_path is None and qsettings is not None:
            presets_path = Path(qsettings.fileName()).with_name("presets.json")
        self.presets = PresetStore(presets_path)
        self.files: list[DataFile] = []
        self.settings: dict[str, CAAnalysisSettings] = {}
        self.cv_settings: dict[str, CVSettings] = {}
        self.responses: list[Response] = []
        self.extras: dict = {}               # analysis_params, study / electrode settings (JSON-able)
        self._curves: dict[str, Curve] = {}
        self.project_path: Optional[Path] = None
        self.meta = ProjectMeta(operator=prefs.get("operator"))
        self.dirty = False

    # ------------------------------------------------------------------ loading
    def add_paths(self, paths: Iterable[str | Path]) -> tuple[list[DataFile], list[str]]:
        loaded: list[DataFile] = []
        errors: list[str] = []
        todo: list[Path] = []
        for p in map(Path, paths):
            if p.is_dir():
                todo += io_palmsens.find_data_files(p)
            elif p.suffix.lower() in io_palmsens.SUPPORTED_SUFFIXES:
                todo.append(p)
            else:
                errors.append(f"{p.name}: unsupported file type")
        for p in todo:
            try:
                f = io_palmsens.load_any(p)
            except Exception as exc:  # noqa: BLE001 - report any parse problem to the user
                errors.append(f"{p.name}: {exc}")
                continue
            for k, c in enumerate(f.curves):
                c.uid = f"{p}#{k}"
            existing = next((j for j, o in enumerate(self.files) if o.path == p), None)
            if existing is None:
                self.files.append(f)
            else:
                self.files[existing] = f
            loaded.append(f)
        self._reindex()
        if loaded:
            self._set_dirty(True)
            self.files_changed.emit()
        return loaded, errors

    def _reindex(self) -> None:
        self._curves = {c.uid: c for f in self.files for c in f.curves}

    def clear(self) -> None:
        """Remove all data and analyses (the project name / path are kept)."""
        self.files.clear()
        self.settings.clear()
        self.cv_settings.clear()
        self.responses.clear()
        self.extras.clear()
        self._curves.clear()
        self._set_dirty(bool(self.project_path))
        self.files_changed.emit()
        self.responses_changed.emit()

    # ------------------------------------------------------------------ project state
    def _set_dirty(self, value: bool) -> None:
        if value != self.dirty:
            self.dirty = value
            self.project_changed.emit()

    def touch(self) -> None:
        """Record that the user changed something worth saving."""
        self._set_dirty(True)

    def display_name(self) -> str:
        if self.meta.name:
            return self.meta.name
        return self.project_path.stem if self.project_path else "Untitled"

    def new_project(self) -> None:
        self.files.clear()
        self.settings.clear()
        self.cv_settings.clear()
        self.responses.clear()
        self.extras.clear()
        self._curves.clear()
        self.project_path = None
        self.meta = ProjectMeta(operator=prefs.get("operator"))
        self.dirty = False
        self.files_changed.emit()
        self.responses_changed.emit()
        self.project_changed.emit()

    def set_meta(self, meta: ProjectMeta) -> None:
        self.meta = meta
        self._set_dirty(True)
        self.project_changed.emit()

    def to_project_data(self, ui: Optional[dict] = None) -> ProjectData:
        known = set(self._curves)
        return ProjectData(
            meta=self.meta, files=list(self.files),
            ca_settings={u: s for u, s in self.settings.items() if u in known},
            cv_settings={u: s for u, s in self.cv_settings.items()
                         if any(r.uid == u for r in self.cv_runs())},
            ui=ui or {}, app_version=__version__, responses=list(self.responses), extras=dict(self.extras),
        )

    def save_project(self, path: str | Path, ui: Optional[dict] = None) -> None:
        path = Path(path)
        self.meta = save_project(path, self.to_project_data(ui))
        self.project_path = path
        self._remember_recent(path)
        self.dirty = False
        self.project_changed.emit()

    def open_project(self, path: str | Path) -> dict:
        """Load and apply a project in one step (raises ``ProjectError``; session untouched on error)."""
        return self.apply_project(load_project(Path(path)), Path(path))

    def apply_project(self, data: ProjectData, path: Path) -> dict:
        """Replace the session with an already-loaded project; returns its saved UI state."""
        self.files = list(data.files)
        self.settings = dict(data.ca_settings)
        self.cv_settings = dict(data.cv_settings)
        self.meta = data.meta
        self.responses = list(data.responses)
        self.extras = dict(data.extras)
        self.project_path = Path(path)
        self._reindex()
        self.dirty = False
        self.files_changed.emit()
        self.responses_changed.emit()
        self._remember_recent(self.project_path)
        self.project_changed.emit()
        return data.ui

    def recent_projects(self) -> list[str]:
        v = self.qs.value("recentProjects", [])
        items = [v] if isinstance(v, str) else list(v or [])
        return [p for p in items if Path(p).is_file()]

    def clear_recent_projects(self) -> None:
        self.qs.remove("recentProjects")

    def _remember_recent(self, path: Path) -> None:
        items = [str(path)] + [p for p in self.recent_projects() if p != str(path)]
        self.qs.setValue("recentProjects", items[:MAX_RECENT])

    def analysis_dir(self, curve: Curve) -> Path:
        """Where "Export analysis" writes: beside the source file, or - if that folder no longer
        exists (e.g. a project opened on another machine) - beside the project, else the home folder."""
        base = analysis_dir_for(curve)
        if Path(curve.source).parent.is_dir():
            return base
        root = self.project_path.parent if self.project_path else Path.home()
        return root / "analysis" / base.name

    # ------------------------------------------------------------------ data files from other sources
    def add_datafile(self, df: DataFile) -> None:
        """Add (or replace, by path) an already-parsed file, e.g. from the table importer."""
        for k, c in enumerate(df.curves):
            c.uid = c.uid or f"{df.path}#{k}"
        existing = next((j for j, o in enumerate(self.files) if o.path == df.path), None)
        if existing is None:
            self.files.append(df)
        else:
            self.files[existing] = df
        self._reindex()
        self._set_dirty(True)
        self.files_changed.emit()

    def set_technique(self, uid: str, key: str) -> None:
        """Override the technique of a measurement (recognition is a guess for some files)."""
        c = self.curve(uid)
        if c is None or c.kind == key:
            return
        c.kind = key
        if key == KIND_CV and c.scan is None:
            c.scan = 1
        self._set_dirty(True)
        self.files_changed.emit()

    # ------------------------------------------------------------------ analyzers and the sensor study
    def get_params(self, analyzer_key: str, uid: str) -> dict:
        a = ANALYZERS[analyzer_key]
        return a.params_for(self.extras.get("analysis_params", {}).get(f"{analyzer_key}|{uid}"))

    def set_params(self, analyzer_key: str, uid: str, params: dict) -> None:
        self.extras.setdefault("analysis_params", {})[f"{analyzer_key}|{uid}"] = dict(params)
        self.touch()

    def add_responses(self, curve: Curve, analyzer_key: str, specs: list[ResponseSpec]) -> list[Response]:
        """Send an analysis's numbers to the study. Existing rows are updated in place (the concentration, group, time,
        role and notes the user typed are kept)."""
        out = upsert_responses(self.responses, curve, analyzer_key, specs)
        self.touch()
        self.responses_changed.emit()
        return out

    def add_manual_response(self, quantity: str, unit: str, value: float, **fields) -> Response:
        n = 1 + max([int(r.id.split(":")[1]) for r in self.responses if r.id.startswith("manual:")] or [0])
        row = Response(id=f"manual:{n}", source_label="manual entry", technique="manual", analyzer="manual",
                       quantity=quantity, unit=unit, value=value, **fields)
        self.responses.append(row)
        self.touch()
        self.responses_changed.emit()
        return row

    def upsert_named(self, rid: str, quantity: str, unit: str, value: float, source_label: str, technique: str,
                     analyzer: str, group: str = "") -> Response:
        """Add or update a number with a stable id (e.g. an electrode's ECSA); fields the user edited are kept."""
        row = next((r for r in self.responses if r.id == rid), None)
        if row is None:
            row = Response(id=rid, group=group, conc=None)
            self.responses.append(row)
        row.quantity, row.unit, row.value = quantity, unit, float(value)
        row.source_label, row.technique, row.analyzer = source_label, technique, analyzer
        if group:
            row.group = group
        self.touch()
        self.responses_changed.emit()
        return row

    def update_response(self, rid: str, **fields) -> None:
        row = next((r for r in self.responses if r.id == rid), None)
        if row is not None:
            for k, v in fields.items():
                setattr(row, k, v)
            self.touch()
            self.responses_changed.emit()

    def remove_responses(self, ids: list[str]) -> None:
        keep = [r for r in self.responses if r.id not in set(ids)]
        if len(keep) != len(self.responses):
            self.responses = keep
            self.touch()
            self.responses_changed.emit()

    # ------------------------------------------------------------------ CV runs
    def cv_runs(self) -> list[CVRun]:
        return group_cv_runs(self.all_curves())

    def get_cv_settings(self, run: CVRun) -> CVSettings:
        s = self.cv_settings.get(run.uid)
        if s is None:
            s = self.cv_settings[run.uid] = CVSettings(scan_rate=run.scan_rate)
        return s

    def cv_settings_for(self, run: CVRun) -> CVSettings:
        """The run's settings for *reading*: the stored ones, else defaults that are not stored. Looking at a run
        (for example in a table) must not make it count as analysed in reports; ``get_cv_settings`` does that."""
        return self.cv_settings.get(run.uid) or CVSettings(scan_rate=run.scan_rate)

    # ------------------------------------------------------------------ lookup
    def curve(self, uid: str) -> Optional[Curve]:
        return self._curves.get(uid)

    def all_curves(self) -> list[Curve]:
        return [c for f in self.files for c in f.curves]

    def ca_curves(self) -> list[Curve]:
        return [c for c in self.all_curves() if c.kind == KIND_CA]

    def file_of(self, curve: Curve) -> Optional[DataFile]:
        return next((f for f in self.files if any(c is curve for c in f.curves)), None)

    # ------------------------------------------------------------------ defaults
    def defaults(self) -> dict:
        out = {}
        for name, typ in DEFAULT_FIELDS:
            v = self.qs.value(f"defaults/{name}")
            if v is None:
                continue
            try:
                out[name] = (str(v).lower() in ("true", "1")) if typ is bool else typ(v)
            except (TypeError, ValueError):
                continue
        return out

    def remember_defaults(self, s: CAAnalysisSettings) -> None:
        for name, _ in DEFAULT_FIELDS:
            self.qs.setValue(f"defaults/{name}", getattr(s, name))

    # ------------------------------------------------------------------ settings per CA run
    def get_settings(self, curve: Curve) -> CAAnalysisSettings:
        s = self.settings.get(curve.uid)
        if s is None:
            s = load_saved_settings(curve) or self._fresh_settings(curve)
            sync_step_lists(s)
            self.settings[curve.uid] = s
        return s

    def _fresh_settings(self, curve: Curve) -> CAAnalysisSettings:
        s = CAAnalysisSettings(**self.defaults())
        parsed = io_palmsens.parse_stock_from_title(curve.meta.get("run_title", ""))
        if parsed:
            s.stock_conc, s.stock_unit = parsed
        self.redetect(curve, s)
        return s

    def redetect(self, curve: Curve, s: CAAnalysisSettings) -> None:
        """(Re)run automatic step detection, resetting the per-step lists."""
        n = len(curve.x) - s.drop_last if s.drop_last > 0 else len(curve.x)
        steps, ignored = detect_step_events(
            curve.x[:n], curve.y[:n], lag=s.detect_lag, k=s.detect_k,
            min_sep=s.detect_min_sep, expected=s.expected_steps,
        )
        s.step_times = [float(t) for t in steps]
        s.ignored_events = [float(t) for t in ignored]
        s.step_volumes_ul, s.step_conc_override, s.step_included = [], [], []
        sync_step_lists(s)
