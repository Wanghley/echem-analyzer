"""Chronoamperometry standard-addition workspace."""
from __future__ import annotations

import bisect
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import QSignalBlocker, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import plots, theme
from .. import standard_addition as sa
from ..ca_analysis import analyze_ca, sync_step_lists
from ..models import CAAnalysisSettings, CAResult, Curve
from ..reporting import g, pm, results_plain_text, summary_rows, write_exports
from .mpl_canvas import PlotWidget, StepLineEditor
from .ui_kit import Card, CollapsibleCard, KpiCard, Splitter, hint, persist_splitters
from .preset_bar import PresetBar
from .session import DEFAULT_FIELDS, Session, curve_run_label

REMEMBER = {name for name, _ in DEFAULT_FIELDS}
DETECT_ONLY = {"detect_k", "detect_min_sep", "expected_steps"}      # only used by "Detect steps"
HINT = ("Drag a dashed line to move a step · double-click to add one · right-click a line to remove it "
        "(turn off the toolbar's zoom/pan tool first). A dotted grey line marked ! is a jump in the "
        "opposite direction that was not counted as an addition.")

class _UnitSpin(QWidget):
    """A number with a unit selector (never an assumed unit). The settings hold mL / µL / µA; choosing another unit
    converts the number on screen and leaves the physical quantity unchanged."""

    value_changed = Signal(float)          # canonical value
    unit_changed = Signal(str)

    def __init__(self, unit_attr: str, table: dict[str, float], lo: float, hi: float, decimals: int = 4, step: float = 0.1, tip: str = ""):
        super().__init__()
        self.unit_attr, self._table, self._lo, self._hi = unit_attr, table, lo, hi
        self._canon = 0.0
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(decimals)
        self.spin.setSingleStep(step)
        self.spin.setKeyboardTracking(False)
        self.spin.setGroupSeparatorShown(False)
        self.combo = QComboBox()
        self.combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.combo.setMinimumWidth(62)
        for u in table:
            self.combo.addItem(u, u)
        if tip:
            self.spin.setToolTip(tip)
        lay.addWidget(self.spin, 1)
        lay.addWidget(self.combo)
        self.spin.valueChanged.connect(self._typed)
        self.combo.currentIndexChanged.connect(self._unit_picked)

    def _factor(self) -> float:
        return sa.factor(self.combo.currentData(), self._table)

    def _show(self) -> None:
        f = self._factor()
        with QSignalBlocker(self.spin):
            self.spin.setRange(self._lo / f, self._hi / f)
            self.spin.setValue(self._canon / f)

    def _typed(self, v: float) -> None:
        self._canon = v * self._factor()                   # the stored quantity is authoritative, not the rounded display
        self.value_changed.emit(self._canon)

    def _unit_picked(self, _i: int) -> None:
        self._show()                                       # same quantity, shown in the new unit
        self.unit_changed.emit(self.combo.currentData())

    def value(self) -> float:
        """The stored quantity (mL, µL or µA, whatever the unit shown)."""
        return self._canon

    def setValue(self, canonical: float) -> None:  # noqa: N802
        """Set the stored quantity (like QDoubleSpinBox.setValue, which the other bound widgets offer)."""
        self.spin.setValue(canonical / self._factor())

    def set_canonical(self, value: float, unit: str) -> None:
        with QSignalBlocker(self.combo):
            i = self.combo.findData(unit)
            self.combo.setCurrentIndex(i if i >= 0 else 0)
        self._canon = float(value)
        self._show()


# table columns
COL_USE, COL_N, COL_T, COL_V, COL_C, COL_OVR, COL_I, COL_DI, COL_SIG = range(9)


class CATab(QWidget):
    def __init__(self, session: Session, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.session = session
        self.curve: Optional[Curve] = None
        self.s: Optional[CAAnalysisSettings] = None
        self.result: Optional[CAResult] = None
        self._loading = False
        self._tbl_updating = False
        self._keep_view = False
        self._binds: dict[str, QWidget] = {}
        self._build_ui()
        session.files_changed.connect(self.refresh_runs)
        theme.bus.changed.connect(self._on_theme)
        self.refresh_runs()

    # ================================================================== UI construction
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        lab = QLabel("RUN")
        lab.setObjectName("CardTitle")
        top.addWidget(lab)
        self.run_combo = QComboBox()
        self.run_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.run_combo.setMinimumContentsLength(30)
        self.run_combo.currentIndexChanged.connect(self._on_run_selected)
        top.addWidget(self.run_combo, 1)
        self.btn_detect = QPushButton("Detect steps")
        self.btn_detect.setToolTip("Re-run automatic step detection (resets per-step volumes and overrides)")
        self.btn_detect.clicked.connect(self.detect)
        self.btn_copy = QPushButton("Copy results")
        self.btn_copy.clicked.connect(self.copy_results)
        self.btn_export = QPushButton("Export analysis…")
        self.btn_export.setToolTip("CSV, PNG and SVG of this run, written next to the data")
        self.btn_export.clicked.connect(self.export_clicked)
        for b in (self.btn_detect, self.btn_copy, self.btn_export):
            top.addWidget(b)
        root.addLayout(top)

        split = Splitter(Qt.Horizontal)
        root.addWidget(split, 1)

        # ---- left: CA plot over (table | calibration)
        left = Splitter(Qt.Vertical)
        ca_card = Card(margins=8)
        self.ca_plot = PlotWidget(figsize=(8, 3.6))
        self.ax_ca = self.ca_plot.fig.add_subplot(111)
        ca_card.body.addWidget(self.ca_plot, 1)
        ca_card.body.addWidget(hint(HINT))
        left.addWidget(ca_card)

        bottom = Splitter(Qt.Horizontal)
        bottom.addWidget(self._build_table_panel())
        cal_card = Card(margins=8)
        self.cal_plot = PlotWidget(figsize=(5, 3.6))
        cal_card.body.addWidget(self.cal_plot, 1)
        bottom.addWidget(cal_card)
        bottom.setStretchFactor(0, 5)
        bottom.setStretchFactor(1, 4)
        bottom.setSizes([700, 420])
        left.addWidget(bottom)
        left.setStretchFactor(0, 5)
        left.setStretchFactor(1, 4)
        split.addWidget(left)

        # ---- right: results and controls
        split.addWidget(self._build_side_panel())
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([1000, 440])            # the unit selectors and the longer result names need room; drag the grip to change it

        self.editor = StepLineEditor(
            self.ca_plot, self.ax_ca,
            get_steps=lambda: self.s.step_times if self.s else [],
            on_move=self._move_step, on_add=self._add_step, on_remove=self._remove_step,
        )

    def _build_table_panel(self) -> QWidget:
        card = Card(margins=10)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["Use", "#", "t (s)", "V (µL)", "C", "C ovr", "I (µA)", "Δi", "Signal"]
        )
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.itemChanged.connect(self._on_table_edit)
        self.table.setToolTip(
            "Row 0 is the baseline (sample before any addition).\n"
            "t = step time, V = volume added (µL), C = added concentration, C ovr = type a\n"
            "concentration to override the calculated one, I = plateau current, Δi = change\n"
            "from the baseline, Signal = polarity-corrected value that is fitted.\n"
            "Untick 'Use' to leave a point out of the fit."
        )
        card.body.addWidget(self.table, 1)
        row = QHBoxLayout()
        b_add = QPushButton("Add step")
        b_add.clicked.connect(self._add_step_mid)
        b_rem = QPushButton("Remove selected step")
        b_rem.clicked.connect(self._remove_selected)
        row.addWidget(b_add)
        row.addWidget(b_rem)
        row.addStretch(1)
        card.body.addLayout(row)
        return card

    def _spin(self, lo, hi, dec=3, step=0.1, suffix="", tip="") -> QDoubleSpinBox:
        sb = QDoubleSpinBox()
        sb.setRange(lo, hi)
        sb.setDecimals(dec)
        sb.setSingleStep(step)
        sb.setKeyboardTracking(False)
        sb.setGroupSeparatorShown(False)
        if suffix:
            sb.setSuffix(f" {suffix}")
        if tip:
            sb.setToolTip(tip)
        return sb

    def _bind(self, attr: str, w: QWidget) -> QWidget:
        self._binds[attr] = w
        if isinstance(w, (QDoubleSpinBox, QSpinBox)):
            w.valueChanged.connect(lambda v, a=attr: self._on_changed(a, v))
        elif isinstance(w, QCheckBox):
            w.toggled.connect(lambda v, a=attr: self._on_changed(a, bool(v)))
        elif isinstance(w, QComboBox):
            w.currentIndexChanged.connect(lambda _i, a=attr, c=w: self._on_changed(a, c.currentData()))
        elif isinstance(w, _UnitSpin):
            w.value_changed.connect(lambda v, a=attr: self._on_changed(a, v))
            w.unit_changed.connect(lambda u, a=w.unit_attr: self._on_unit_changed(a, u))
        return w

    def _build_side_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(2, 2, 10, 2)
        lay.setSpacing(10)

        # -- headline figures
        self.kpi_sens, self.kpi_cx = KpiCard("Sensitivity"), KpiCard("Cₓ (x-intercept)")
        self.kpi_lod, self.kpi_r2 = KpiCard("LOD"), KpiCard("R²")
        grid = QGridLayout()
        grid.setSpacing(8)
        for k, w in enumerate((self.kpi_sens, self.kpi_cx, self.kpi_lod, self.kpi_r2)):
            grid.addWidget(w, k // 2, k % 2)
        lay.addLayout(grid)

        # -- details
        card = Card("Results")
        self.results_label = QLabel()
        self.results_label.setTextFormat(Qt.RichText)
        self.results_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.results_label.setWordWrap(True)
        card.body.addWidget(self.results_label)
        lay.addWidget(card)

        # -- saved setups
        card = Card("Setup presets")
        self.preset_bar = PresetBar(
            self.session, "ca", get_current=lambda: self.s,
            get_all=lambda: [self.session.get_settings(c) for c in self.session.ca_curves()],
        )
        self.preset_bar.applied.connect(self._on_preset_applied)
        card.body.addWidget(self.preset_bar)
        lay.addWidget(card)

        # -- additions: every quantity with a unit selector
        card = Card("Additions")
        f = QFormLayout()
        f.setLabelAlignment(Qt.AlignLeft)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._bind("stock_conc", self._spin(0, 1e9, 4, 1, "", "Concentration of the stock you pipetted in.")), 1)
        cb = QComboBox()
        for u in sa.STOCK_UNITS:
            cb.addItem(u, u)
        row.addWidget(self._bind("stock_unit", cb))
        rw = QWidget()
        rw.setLayout(row)
        f.addRow("Stock conc.", rw)
        f.addRow("Cell volume V₀", self._bind("v0_ml", _UnitSpin("v0_unit", sa.VOLUME_ML, 0, 1e5, 4, 0.5,
                 "Total solution volume in the cell before the first addition (sample plus any other reagent).")))
        f.addRow("Sample volume", self._bind("v_sample_ml", _UnitSpin("v_sample_unit", sa.VOLUME_ML, 0, 1e5, 4, 0.5,
                 "Volume of the original sample inside V₀ (0 = V₀, i.e. the sample is not mixed with anything).\n"
                 "A 1:1 mix with buffer: V_sample = V₀/2, so C_sample = 2·Cₓ.")))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._bind("v_add_ul", _UnitSpin("v_add_unit", sa.VOLUME_UL, 0, 1e6, 3, 1, "Nominal volume per addition.")), 1)
        b = QPushButton("All steps")
        b.setToolTip("Set every step's volume to this value.")
        b.clicked.connect(self._apply_vadd_all)
        row.addWidget(b)
        rw = QWidget()
        rw.setLayout(row)
        f.addRow("V add (each)", rw)
        f.addRow("Verified volume", self._bind("verified_ul", _UnitSpin("verified_unit", sa.VOLUME_UL, 0, 1e6, 3, 0.1,
                 "The volume the pipette really dispensed (gravimetric or other check). Used instead of the nominal volume for\n"
                 "every step still at the nominal value; 0 = no pipette check.")))
        cb = QComboBox()
        for key, label in (("concentration", "Concentration"), ("signal", "Signal scaling"), ("none", "None")):
            cb.addItem(label, key)
        cb.setToolTip("Exactly one correction is applied, never both:\n"
                      "Concentration: x = c·nv/(V₀+nv). Corrects the added standard only.\n"
                      "Signal scaling: x = c·nv/V₀ and the signal × (V₀+ΣV)/V₀. Exact, also for the sample's own dilution.\n"
                      "None: no correction (only sensible when the additions are a tiny fraction of V₀).")
        f.addRow("Dilution", self._bind("dilution_mode", cb))
        bg = _UnitSpin("bkg_unit", sa.CURRENT_UA, -1e6, 1e6, 4, 0.01,
                       "Magnitude of the blank current subtracted from |i| (a blank run at the same potential and time).\n"
                       "Without it the x-intercept is shifted by background / slope.")
        f.addRow("Background |i|", self._bind("background", bg))
        f.addRow("Electrode area", self._bind("area_cm2", self._spin(0, 1e3, 5, 0.01, "cm²",
                 "Optional: used only to report the sensitivity per cm².")))
        card.body.addLayout(f)
        lay.addWidget(card)

        # -- response
        card = Card("Response per addition")
        f = QFormLayout()
        cb = QComboBox()
        cb.addItem("Plateau mean (end of segment)", "plateau")
        cb.addItem("Drift-corrected (pre/post lines)", "drift")
        cb.setToolTip(
            "Plateau mean: average the last W seconds before the next addition.\n"
            "Drift-corrected: extrapolate straight lines fitted before and after each step to the\n"
            "step time and use the jump, which removes slow current decay/drift inside a plateau."
        )
        f.addRow("Method", self._bind("method", cb))
        f.addRow("Settle time", self._bind("settle", self._spin(0, 600, 1, 0.5, "s", "Skipped after each step.")))
        f.addRow("Averaging window", self._bind("window", self._spin(0.2, 600, 1, 0.5, "s")))
        f.addRow("End guard", self._bind("end_guard", self._spin(0, 10, 1, 0.1, "s", "Excluded just before the next step.")))
        card.body.addLayout(f)
        lay.addWidget(card)

        # -- fit / LOD
        card = Card("Fit and LOD")
        f = QFormLayout()
        cb = QComboBox()
        for key in sa.SIGMA_SOURCES:
            cb.addItem({"fit": "Residual s(y/x) (default)", "baseline": "Baseline noise of this trace", "blank": "Blank replicates (SD)"}[key], key)
        cb.setToolTip("σ for LOD and LOQ. All three are always listed in the results; this picks the one behind the headline LOD.")
        f.addRow("σ for LOD/LOQ", self._bind("sigma_source", cb))
        f.addRow("Baseline window", self._bind("baseline_window", self._spin(1, 600, 1, 1, "s", "Length of the trace before the first addition used for the baseline noise.")))
        self.blank_edit = QLineEdit()
        self.blank_edit.setPlaceholderText("e.g. 0.011, 0.012, 0.010")
        self.blank_edit.setToolTip("Currents measured in blank replicates (same unit as the background). Their standard deviation is σ for the 'blank' source; at least 2.")
        self.blank_edit.editingFinished.connect(self._on_blank_edited)
        f.addRow("Blank replicates", self.blank_edit)
        f.addRow("LOD factor k", self._bind("lod_k", self._spin(1, 10, 1, 0.1, "", "LOD = k·σ/|b|. 3.3 follows ICH, 3 is the older IUPAC-style value.")))
        f.addRow("LOQ factor", self._bind("loq_k", self._spin(1, 30, 1, 1, "", "LOQ = factor·σ/|b| (10 by convention).")))
        f.addRow("Suitability min.", self._bind("suitability_min", self._spin(0, 1000, 1, 1, "",
                 "b·√Sxx / s(y) below this flags the extrapolation as unreliable (default 12).")))
        f.addRow("Plausible max. C", self._bind("plausible_max", self._spin(0, 1e12, 4, 1, "",
                 "Largest sample concentration you would believe, in the stock's unit (0 = no check). A result above it suggests a wrong unit.")))
        f.addRow(self._bind("include_baseline", QCheckBox("Include baseline point in fit")))
        f.addRow(self._bind("compare_baseline", QCheckBox("Also show the result with the baseline point switched")))
        card.body.addLayout(f)
        lay.addWidget(card)

        # -- advanced (folded away by default)
        adv = CollapsibleCard("Advanced: preprocessing and step detection", expanded=False)
        f = QFormLayout()
        sp = QSpinBox()
        sp.setRange(0, 100)
        sp.setToolTip("Discard this many trailing points (PalmSens often records a spike at the very end).")
        f.addRow("Drop last N points", self._bind("drop_last", sp))
        self.smooth_spin = QSpinBox()
        self.smooth_spin.setRange(1, 101)
        self.smooth_spin.setSingleStep(2)
        self.smooth_spin.setToolTip("Moving-average overlay on the plot (display only, not used in the analysis).")
        self.smooth_spin.valueChanged.connect(lambda _v: self.recompute(user_edit=False))   # display only
        f.addRow("Display smoothing (pts)", self.smooth_spin)
        f.addRow("Threshold (× noise σ)", self._bind("detect_k", self._spin(2, 100, 1, 1)))
        f.addRow("Min. separation", self._bind("detect_min_sep", self._spin(0.5, 600, 1, 1, "s")))
        sp = QSpinBox()
        sp.setRange(0, 50)
        sp.setSpecialValueText("auto")
        sp.setToolTip("Keep only the N largest steps (0 = automatic).")
        f.addRow("Expected steps", self._bind("expected_steps", sp))
        adv.content_layout.addLayout(f)
        lay.addWidget(adv)
        lay.addStretch(1)

        self.panel = panel
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(280)                       # the user can drag the grip to make it wider or narrower
        scroll.setFrameShape(QFrame.NoFrame)
        return scroll

    # ================================================================== run selection
    def refresh_runs(self) -> None:
        cur_uid = self.curve.uid if self.curve else None
        with QSignalBlocker(self.run_combo):
            self.run_combo.clear()
            for c in self.session.ca_curves():
                self.run_combo.addItem(f"{curve_run_label(c)}  ({c.n} pts)", c.uid)
            idx = self.run_combo.findData(cur_uid) if cur_uid else -1
            self.run_combo.setCurrentIndex(idx if idx >= 0 else (0 if self.run_combo.count() else -1))
        has = self.run_combo.count() > 0
        self.panel.setEnabled(has)
        for b in (self.btn_detect, self.btn_export, self.btn_copy):
            b.setEnabled(has)
        if not has:
            self.curve = self.s = self.result = None
            self._show_empty()
            return
        uid = self.run_combo.currentData()
        if self.curve is None or self.curve.uid != uid or self.session.curve(uid) is not self.curve:
            self.load_curve(self.session.curve(uid))

    def select_curve(self, uid: str) -> None:
        i = self.run_combo.findData(uid)
        if i >= 0:
            self.run_combo.setCurrentIndex(i)

    def _on_run_selected(self, _i: int) -> None:
        uid = self.run_combo.currentData()
        c = self.session.curve(uid) if uid else None
        if c is not None:
            self.load_curve(c)

    def load_curve(self, curve: Curve) -> None:
        self.curve = curve
        self.s = self.session.get_settings(curve)
        self._populate_controls()
        self._keep_view = False
        self.ca_plot.reset_toolbar()
        self.recompute(user_edit=False)

    # ---- project state (saved in the project file) and presets
    def get_state(self) -> dict:
        return {"uid": self.curve.uid if self.curve else None}

    def set_state(self, state: dict) -> None:
        uid = (state or {}).get("uid")
        if uid:
            self.select_curve(uid)

    def _on_preset_applied(self, _scope: str) -> None:
        if self.s is None:
            return
        self._populate_controls()
        self.session.remember_defaults(self.s)
        self.recompute(user_edit=True)

    def _populate_controls(self) -> None:
        self._loading = True
        try:
            for attr, w in self._binds.items():
                val = getattr(self.s, attr)
                if isinstance(w, (QDoubleSpinBox, QSpinBox)):
                    w.setValue(val)
                elif isinstance(w, QCheckBox):
                    w.setChecked(bool(val))
                elif isinstance(w, QComboBox):
                    i = w.findData(val)
                    w.setCurrentIndex(i if i >= 0 else 0)
                elif isinstance(w, _UnitSpin):
                    w.set_canonical(val, getattr(self.s, w.unit_attr))
            self._show_blank()
        finally:
            self._loading = False

    def _show_empty(self) -> None:
        self.ax_ca.clear()
        self.ax_ca.text(0.5, 0.5, "Open a PalmSens .csv or .pssession file\n(File → Add data files…, or drag it onto this window)",
                        ha="center", va="center", transform=self.ax_ca.transAxes, color="#6b7280")
        self.ax_ca.set_axis_off()
        self.ca_plot.redraw()
        self.cal_plot.fig.clear()
        self.cal_plot.redraw()
        self.table.setRowCount(0)
        self.results_label.setText("")

    # ================================================================== user edits
    def _on_changed(self, attr: str, value) -> None:
        if self._loading or self.s is None:
            return
        setattr(self.s, attr, value)
        if attr in REMEMBER:
            self.session.remember_defaults(self.s)
        if attr in DETECT_ONLY:
            return
        self.recompute()

    def _on_unit_changed(self, unit_attr: str, unit: str) -> None:
        self._on_changed(unit_attr, unit)
        if unit_attr == "bkg_unit":
            self._show_blank()                                  # the replicate currents are shown in the background's unit

    def _show_blank(self) -> None:
        unit = self.s.bkg_unit if self.s is not None else "µA"
        vals = self.s.blank_replicates if self.s is not None else []
        self.blank_edit.setText(", ".join(f"{sa.from_canonical(v, unit, sa.CURRENT_UA):.6g}" for v in vals))

    def _on_blank_edited(self) -> None:
        if self._loading or self.s is None:
            return
        import re
        try:
            nums = [float(x) for x in re.split(r"[,;\s]+", self.blank_edit.text().strip()) if x]      # decimal point, not comma
        except ValueError:
            self._show_blank()                                  # not numbers: put the stored values back
            return
        self._on_changed("blank_replicates", [sa.to_canonical(v, self.s.bkg_unit, sa.CURRENT_UA) for v in nums])
        self._show_blank()

    def detect(self) -> None:
        if self.curve is None or self.s is None:
            return
        s = self.s
        edited = any(v != s.v_add_ul for v in s.step_volumes_ul) or any(o is not None for o in s.step_conc_override)
        if edited and QMessageBox.question(
            self, "Detect steps",
            "This resets the per-step volumes, concentration overrides and inclusion flags. Continue?",
        ) != QMessageBox.Yes:
            return
        self.session.redetect(self.curve, s)
        self.recompute()

    def _apply_vadd_all(self) -> None:
        if self.s is None:
            return
        self.s.step_volumes_ul = [self.s.v_add_ul] * len(self.s.step_times)
        self.recompute()

    def _move_step(self, idx: int, t: float) -> None:
        if self.s is None or self.curve is None:
            return
        t = float(np.clip(t, self.curve.x[0] + 1.0, self.curve.x[-1] - 1.0))
        self.s.step_times[idx] = t
        self.recompute()

    def _add_step(self, t: float) -> None:
        if self.s is None:
            return
        s = self.s
        pos = bisect.bisect(s.step_times, t)
        s.step_times.insert(pos, float(t))
        s.step_volumes_ul.insert(pos, s.v_add_ul)
        s.step_conc_override.insert(pos, None)
        s.step_included.insert(pos, True)
        self.recompute()

    def _add_step_mid(self) -> None:
        if self.s is None:
            return
        lo, hi = self.ax_ca.get_xlim()
        self._add_step(0.5 * (lo + hi))

    def _remove_step(self, idx: int) -> None:
        s = self.s
        if s is None or not (0 <= idx < len(s.step_times)):
            return
        for lst in (s.step_times, s.step_volumes_ul, s.step_conc_override, s.step_included):
            del lst[idx]
        self.recompute()

    def _remove_selected(self) -> None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        if rows and min(rows) >= 1:
            self._remove_step(min(rows) - 1)

    def _on_table_edit(self, item: QTableWidgetItem) -> None:
        if self._tbl_updating or self.s is None:
            return
        s, row, col = self.s, item.row(), item.column()
        try:
            if col == COL_USE:
                on = item.checkState() == Qt.Checked
                if row == 0:
                    s.include_baseline = on
                    with QSignalBlocker(self._binds["include_baseline"]):
                        self._binds["include_baseline"].setChecked(on)
                else:
                    s.step_included[row - 1] = on
            elif row >= 1 and col in (COL_T, COL_V, COL_OVR):
                txt = item.text().strip().replace(",", ".")
                if col == COL_OVR:
                    s.step_conc_override[row - 1] = float(txt) if txt else None
                elif col == COL_T:
                    s.step_times[row - 1] = float(txt)
                else:
                    s.step_volumes_ul[row - 1] = float(txt)
        except ValueError:
            pass          # fall through: recompute rewrites the cell with the stored value
        self.recompute()

    # ================================================================== recompute + render
    def recompute(self, user_edit: bool = True) -> None:
        """Re-run the analysis and redraw. ``user_edit=False`` for mere viewing (opening a run,
        display-only options): those must not mark the project as having unsaved changes."""
        if self._loading or self.curve is None or self.s is None:
            return
        if user_edit:
            self.session.touch()
        sync_step_lists(self.s)
        self.result = analyze_ca(self.curve.x, self.curve.y, self.s)
        self._fill_table()
        self._draw_ca()
        self._draw_cal()
        self._fill_results()
        self.session.analysis_changed.emit(self.curve.uid)

    def _fill_table(self) -> None:
        s, r = self.s, self.result
        self._tbl_updating = True
        try:
            n = len(r.plateau)
            self.table.setRowCount(n)

            def put(row, col, text, editable=False, checked=None, align=Qt.AlignRight):
                it = QTableWidgetItem(text)
                flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
                if editable:
                    flags |= Qt.ItemIsEditable
                if checked is not None:
                    flags |= Qt.ItemIsUserCheckable
                    it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
                it.setFlags(flags)
                it.setTextAlignment(align | Qt.AlignVCenter)
                self.table.setItem(row, col, it)

            for k in range(n):
                put(k, COL_USE, "", checked=bool(r.included[k]))
                put(k, COL_N, "base" if k == 0 else str(k), align=Qt.AlignCenter)
                if k == 0:
                    put(k, COL_T, "—")
                    put(k, COL_V, "—")
                    put(k, COL_OVR, "")
                else:
                    ov = s.step_conc_override[k - 1]
                    put(k, COL_T, f"{r.step_times[k - 1]:.2f}", editable=True)
                    put(k, COL_V, f"{s.step_volumes_ul[k - 1]:g}", editable=True)
                    put(k, COL_OVR, "" if ov is None else f"{ov:g}", editable=True)
                put(k, COL_C, g(r.conc_added[k], 5))
                put(k, COL_I, g(r.plateau[k], 5))
                put(k, COL_DI, g(r.delta[k], 4))
                put(k, COL_SIG, g(r.signal[k], 5))
            self.table.horizontalHeaderItem(COL_C).setText(f"C ({s.stock_unit})")
            self.table.horizontalHeaderItem(COL_OVR).setText("C ovr")
            self.table.resizeColumnsToContents()
        finally:
            self._tbl_updating = False

    def _draw_ca(self) -> None:
        ax, c, s, r = self.ax_ca, self.curve, self.s, self.result
        view = (ax.get_xlim(), ax.get_ylim()) if self._keep_view else None
        f = self.session.file_of(c)
        plots.ca_trace(ax, c, s, r, theme.current(), smooth=self.smooth_spin.value(),
                       title=f"{f.name if f else ''} — {c.label}")
        if view is not None:
            ax.set_xlim(view[0])
            ax.set_ylim(view[1])
        self._keep_view = True
        self.ca_plot.redraw()

    def _draw_cal(self) -> None:
        plots.ca_calibration(self.cal_plot.fig, self.s, self.result, theme.current())
        self.cal_plot.redraw()

    def _on_theme(self) -> None:
        if self.curve is not None and self.s is not None and self.result is not None:
            self.recompute(user_edit=False)

    def _fill_results(self) -> None:
        s, r, p = self.s, self.result, theme.current()
        f, u = r.fit, s.stock_unit
        if f is not None:
            r2_tone = "success" if f.r2 >= 0.995 else ("warning" if f.r2 < 0.98 else "")
            self.kpi_sens.set(g(f.slope, 4), f"µA/{u}  ± {g(f.slope_se, 2)}")
            self.kpi_cx.set(g(r.c_x, 4), f"± {g(r.c_x_ci95, 2)} {u} (95 % CI)")
            self.kpi_lod.set(g(r.lod, 3), f"{u} · LOQ {g(r.loq, 3)}")
            self.kpi_r2.set(g(f.r2, 4), f"{f.n} points in the fit", r2_tone)
        else:
            for k, hint_text in ((self.kpi_sens, "needs concentrations"), (self.kpi_cx, ""), (self.kpi_lod, ""), (self.kpi_r2, "")):
                k.set("—", hint_text)
        muted = f"color:{p.muted}"
        parts = ["<table cellspacing='3'>"]
        for name, val, unit in summary_rows(s, r):
            parts.append(
                f"<tr><td style='{muted}'>{name}</td><td align='right'><b>{val}</b></td>"
                f"<td style='{muted}'>&nbsp;{unit}</td></tr>"
            )
        parts.append("</table>")
        if r.fit is None:
            parts.append(f"<p style='{muted}'>Enter stock concentration, V₀ and addition volumes "
                         "(or per-step concentration overrides) to fit the calibration.</p>")
        for w in r.warnings:
            parts.append(f"<p style='color:{p.warning}'>⚠ {w}</p>")
        self.results_label.setText("".join(parts))

    # ================================================================== export / copy
    def title_for_report(self) -> str:
        f = self.session.file_of(self.curve) if self.curve else None
        return f"{f.name if f else ''} — {self.curve.label}" if self.curve else ""

    def copy_results(self) -> None:
        if self.result is None:
            return
        QGuiApplication.clipboard().setText(results_plain_text(self.title_for_report(), self.s, self.result))

    def export_to(self, out_dir: Path) -> list[Path]:
        """Write CSV/JSON/PNG/SVG for the current run into ``out_dir`` and return the paths."""
        written = write_exports(out_dir, self.curve, self.s, self.result)
        for name, plot in (("ca_plot", self.ca_plot), ("calibration", self.cal_plot)):
            for ext in ("png", "svg"):
                p = out_dir / f"{name}.{ext}"
                plot.fig.savefig(p)
                written.append(p)
        return written

    def export_clicked(self) -> None:
        if self.curve is None or self.result is None:
            return
        out = self.session.analysis_dir(self.curve)
        try:
            self.export_to(out)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", f"{out}\n\n{exc}")
            return
        box = QMessageBox(QMessageBox.Information, "Analysis exported", f"Saved to:\n{out}", QMessageBox.Ok, self)
        open_btn = box.addButton("Open folder", QMessageBox.ActionRole)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(out)))
