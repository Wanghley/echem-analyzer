"""Sensor study: the Responses table and calibration / reproducibility / stability / selectivity / recovery."""
from __future__ import annotations

import math
from dataclasses import asdict, fields
from typing import Callable, Optional

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import calibration as cal
from .. import plots, theme
from .. import study_analysis as sa
from ..analyzers import g
from ..models import Response
from ..prefs import prefs
from .mpl_canvas import PlotWidget
from .session import Session
from .ui_kit import Card, KpiCard, Notice, Splitter, hint, persist_splitters

ALL_GROUPS = "All electrodes (pooled)"
ALL_CONC = "All concentrations"
MODELS = (("Automatic (lowest AIC)", "auto"), ("Linear", "linear"), ("Log-linear (Nernstian)", "loglinear"),
          ("Michaelis–Menten / Langmuir", "michaelis"), ("Hill", "hill"))


def fill_combo(cb: QComboBox, items: list[str], keep: Optional[str] = None, first: Optional[str] = None) -> None:
    """Refill a combo box without firing signals, keeping the selection when it still exists."""
    cur = keep if keep is not None else cb.currentText()
    with QSignalBlocker(cb):
        cb.clear()
        if first:
            cb.addItem(first, "")
        for it in items:
            cb.addItem(it, it)
        i = cb.findText(cur)
        cb.setCurrentIndex(i if i >= 0 else 0)


def fill_time_combo(cb: QComboBox, responses, quantity: str, keep=None) -> None:
    """All times / each distinct time / (no time): which measurement session a study uses."""
    cur = keep if keep is not None else cb.currentData()
    ts = sa.times(responses, quantity)
    has_unset = any(r.time is None for r in sa.usable(responses) if r.quantity == quantity)
    with QSignalBlocker(cb):
        cb.clear()
        cb.addItem("All times", None)
        if has_unset and ts:
            cb.addItem("(no time set)", sa.NO_TIME)
        for t in ts:
            cb.addItem(f"time = {t:g}", t)
        i = cb.findData(cur)
        cb.setCurrentIndex(i if i >= 0 else 0)


def table_into(table: QTableWidget, header: list[str], rows: list[list[str]]) -> None:
    table.setColumnCount(len(header))
    table.setHorizontalHeaderLabels(header)
    table.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            it = QTableWidgetItem(str(v))
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            it.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter if j == 0 else Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(i, j, it)
    table.resizeColumnsToContents()


def make_table() -> QTableWidget:
    t = QTableWidget(0, 0)
    t.setFrameShape(QFrame.NoFrame)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(28)
    t.setShowGrid(False)
    t.setAlternatingRowColors(True)
    t.horizontalHeader().setStretchLastSection(True)
    return t


# =============================================================================== dialogs

class ManualDialog(QDialog):
    """Type a number that did not come from a measurement (e.g. a value from a paper or another instrument)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add a number manually")
        form = QFormLayout(self)
        self.quantity = QLineEdit("Peak height")
        self.unit = QLineEdit("µA")
        self.value = QDoubleSpinBox()
        self.value.setRange(-1e12, 1e12)
        self.value.setDecimals(6)
        self.conc = QDoubleSpinBox()
        self.conc.setRange(0, 1e12)
        self.conc.setDecimals(6)
        self.conc_unit = QComboBox()
        self.conc_unit.addItems(sa.CONC_UNITS)
        self.conc_unit.setCurrentText(prefs.get("conc_unit"))
        self.condition = QLineEdit("analyte")
        self.group = QLineEdit()
        for label, w in (("Quantity", self.quantity), ("Unit", self.unit), ("Value", self.value), ("Concentration", self.conc),
                         ("Concentration unit", self.conc_unit), ("Condition", self.condition), ("Electrode / group", self.group)):
            form.addRow(label, w)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        form.addRow(box)

    def values(self) -> dict:
        return dict(quantity=self.quantity.text().strip() or "Value", unit=self.unit.text().strip(), value=self.value.value(),
                    conc=self.conc.value() or None, conc_unit=self.conc_unit.currentText(),
                    condition=self.condition.text().strip() or "analyte", group=self.group.text().strip())


class BulkDialog(QDialog):
    """Set a field on many selected rows at once; empty fields are left unchanged."""

    def __init__(self, n: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Set values for {n} selected row(s)")
        form = QFormLayout(self)
        self.condition, self.group, self.time = QLineEdit(), QLineEdit(), QLineEdit()
        self.conc = QLineEdit()
        self.conc_unit = QComboBox()
        self.conc_unit.addItems(["(unchanged)"] + sa.CONC_UNITS)
        self.role = QComboBox()
        self.role.addItems(["(unchanged)"] + list(sa.ROLES))
        for label, w in (("Concentration", self.conc), ("Concentration unit", self.conc_unit), ("Condition", self.condition),
                         ("Electrode / group", self.group), ("Time", self.time), ("Role", self.role)):
            form.addRow(label, w)
        form.addRow(hint("Leave a field empty to keep each row's current value."))
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        form.addRow(box)

    def changes(self) -> dict:
        out: dict = {}
        for key, w in (("condition", self.condition), ("group", self.group)):
            if w.text().strip():
                out[key] = w.text().strip()
        for key, w in (("conc", self.conc), ("time", self.time)):
            if w.text().strip():
                try:
                    out[key] = float(w.text().strip().replace(",", "."))
                except ValueError:
                    pass
        if self.conc_unit.currentIndex() > 0:
            out["conc_unit"] = self.conc_unit.currentText()
        if self.role.currentIndex() > 0:
            out["role"] = self.role.currentText()
        return out


# =============================================================================== responses table

COLS = ["Use", "Source", "Technique", "Quantity", "Value", "Unit", "Conc", "Conc unit", "Condition", "Group", "Time", "Role", "Note"]
C_USE, C_SRC, C_TECH, C_QTY, C_VAL, C_UNIT, C_CONC, C_CUNIT, C_COND, C_GRP, C_TIME, C_ROLE, C_NOTE = range(13)


class ResponsesPanel(QWidget):
    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self._updating = False
        self._rows: list[Response] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(8)
        bar = QHBoxLayout()
        intro = hint("Numbers extracted from your measurements. Give them a concentration, an electrode (group), a condition and a time: "
                     "every study is computed from this table.")
        bar.addWidget(intro, 1)
        self.filter = QComboBox()
        self.filter.currentIndexChanged.connect(self.rebuild)
        bar.addWidget(self.filter)
        for text, fn in (("Add number…", self.add_manual), ("Set for selected…", self.bulk), ("Remove selected", self.remove)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        lay.addLayout(bar)
        card = Card(margins=10)
        self.table = make_table()
        self.table.setColumnCount(len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.itemChanged.connect(self._edited)
        self.table.setToolTip("Role: standard = a calibration point, blank = a measurement without analyte (sets σ), "
                              "sample = an unknown (its concentration is predicted; an expected concentration gives a recovery).")
        card.body.addWidget(self.table, 1)
        self.empty = hint("The table is empty. Open the Analysis tab, run an analysis on a measurement and press “Add to study”.")
        card.body.addWidget(self.empty)
        lay.addWidget(card, 1)
        session.responses_changed.connect(self.rebuild)
        self.rebuild()

    def rebuild(self) -> None:
        fill_combo(self.filter, sa.quantities(self.session.responses) + sorted({r.quantity for r in self.session.responses} - set(sa.quantities(self.session.responses))),
                   first="All quantities")
        q = self.filter.currentText()
        self._rows = [r for r in self.session.responses if self.filter.currentIndex() == 0 or r.quantity == q]
        self._updating = True
        try:
            self.table.setRowCount(len(self._rows))
            for i, r in enumerate(self._rows):
                def put(col, text, edit=False, check=None):
                    it = QTableWidgetItem(text)
                    fl = Qt.ItemIsEnabled | Qt.ItemIsSelectable | (Qt.ItemIsEditable if edit else Qt.NoItemFlags)
                    if check is not None:
                        fl |= Qt.ItemIsUserCheckable
                        it.setCheckState(Qt.Checked if check else Qt.Unchecked)
                    it.setFlags(fl)
                    if col in (C_VAL, C_CONC, C_TIME):
                        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.table.setItem(i, col, it)
                put(C_USE, "", check=r.use)
                put(C_SRC, r.source_label)
                put(C_TECH, r.technique)
                put(C_QTY, r.quantity)
                put(C_VAL, g(r.value, 5) + (f" ± {g(r.sd, 2)}" if r.sd else ""))
                put(C_UNIT, r.unit)
                put(C_CONC, "" if r.conc is None else f"{r.conc:g}", edit=True)
                put(C_CUNIT, r.conc_unit, edit=True)
                put(C_COND, r.condition, edit=True)
                put(C_GRP, r.group, edit=True)
                put(C_TIME, "" if r.time is None else f"{r.time:g}", edit=True)
                put(C_ROLE, r.role, edit=True)
                put(C_NOTE, r.note, edit=True)
            self.table.resizeColumnsToContents()
            self.table.setColumnWidth(C_SRC, min(self.table.columnWidth(C_SRC), 300))
        finally:
            self._updating = False
        self.empty.setVisible(not self.session.responses)

    def _edited(self, it: QTableWidgetItem) -> None:
        if self._updating or it.row() >= len(self._rows):
            return
        r, col, text = self._rows[it.row()], it.column(), it.text().strip()
        try:
            if col == C_USE:
                self.session.update_response(r.id, use=it.checkState() == Qt.Checked)
            elif col in (C_CONC, C_TIME):
                val = None if not text else float(text.replace(",", "."))
                self.session.update_response(r.id, **{"conc" if col == C_CONC else "time": val})
            elif col == C_CUNIT:
                self.session.update_response(r.id, conc_unit=text if text in sa.CONC_FACTORS else r.conc_unit)
            elif col == C_ROLE:
                self.session.update_response(r.id, role=text if text in sa.ROLES else r.role)
            elif col == C_COND:
                self.session.update_response(r.id, condition=text or "analyte")
            elif col == C_GRP:
                self.session.update_response(r.id, group=text)
            elif col == C_NOTE:
                self.session.update_response(r.id, note=text)
        except ValueError:
            pass
        self.rebuild()                                          # rewrite the cell with the stored value

    def selected_ids(self) -> list[str]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self._rows[r].id for r in rows if r < len(self._rows)]

    def add_manual(self) -> None:
        dlg = ManualDialog(self)
        if dlg.exec():
            v = dlg.values()
            self.session.add_manual_response(v.pop("quantity"), v.pop("unit"), v.pop("value"), **v)

    def bulk(self) -> None:
        ids = self.selected_ids()
        if not ids:
            QMessageBox.information(self, "Set values", "Select one or more rows first.")
            return
        dlg = BulkDialog(len(ids), self)
        if dlg.exec():
            ch = dlg.changes()
            for rid in ids:
                self.session.update_response(rid, **ch)

    def remove(self) -> None:
        ids = self.selected_ids()
        if ids:
            self.session.remove_responses(ids)


# =============================================================================== analysis panels

class _Panel(QWidget):
    """Shared layout: controls + headline tiles on the right, a figure and a table on the left."""
    key = ""
    figsize = (7.0, 4.4)

    def __init__(self, session: Session, n_kpis: int = 4):
        super().__init__()
        self.session = session
        self._loading = True
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        split = Splitter(Qt.Horizontal)
        lay.addWidget(split)
        left = Splitter(Qt.Vertical)
        pc = Card(margins=8)
        self.plot = PlotWidget(figsize=self.figsize)
        pc.body.addWidget(self.plot, 1)
        left.addWidget(pc)
        tc = Card(margins=10)
        self.table = make_table()
        tc.body.addWidget(self.table, 1)
        left.addWidget(tc)
        left.setStretchFactor(0, 4)
        left.setStretchFactor(1, 2)
        left.setSizes([520, 220])
        split.addWidget(left)
        panel = QWidget()
        self.side = QVBoxLayout(panel)
        self.side.setContentsMargins(2, 2, 10, 2)
        self.side.setSpacing(10)
        self.kpis = [KpiCard("—") for _ in range(n_kpis)]
        grid = QGridLayout()
        grid.setSpacing(8)
        for k, w in enumerate(self.kpis):
            grid.addWidget(w, k // 2, k % 2)
        self.side.addLayout(grid)
        self.notice = Notice()
        self.side.addWidget(self.notice)
        self.controls = Card("Settings")
        self.form = QFormLayout()
        self.controls.body.addLayout(self.form)
        self.side.addWidget(self.controls)
        self.side.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(280)                       # the user can drag the grip to make it wider or narrower
        scroll.setFrameShape(QFrame.NoFrame)
        split.addWidget(scroll)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([1000, 372])
        session.responses_changed.connect(self.refresh)
        theme.bus.changed.connect(self.recompute)

    # -- persistence of this panel's settings in the project
    def stored(self) -> dict:
        return self.session.extras.get("study", {}).get(self.key, {})

    def store(self, opts: dict) -> None:
        self.session.extras.setdefault("study", {})[self.key] = opts
        self.session.touch()

    def refresh(self) -> None:                                  # refill the choices, then recompute
        raise NotImplementedError

    def recompute(self) -> None:
        raise NotImplementedError

    def set_kpis(self, items: list[tuple[str, str, str, str]]) -> None:
        for k, card in enumerate(self.kpis):
            if k < len(items):
                label, value, sub, tone = items[k]
                card.label.setText(label.upper())
                card.set(value, sub, tone)
                card.setVisible(True)
            else:
                card.setVisible(False)

    def show_warnings(self, warnings: list[str]) -> None:
        if warnings:
            self.notice.show_message("<br>".join("⚠ " + w for w in warnings), "warning")
        else:
            self.notice.setVisible(False)


class CalibrationPanel(_Panel):
    key = "calibration"

    def __init__(self, session: Session):
        super().__init__(session)
        self.study: Optional[sa.CalibrationStudy] = None
        self.quantity, self.condition, self.group, self.time = QComboBox(), QComboBox(), QComboBox(), QComboBox()
        self.model, self.unit, self.xscale, self.sigma = QComboBox(), QComboBox(), QComboBox(), QComboBox()
        for text, val in MODELS:
            self.model.addItem(text, val)
        self.unit.addItem("Automatic", "")
        for u in sa.CONC_UNITS:
            self.unit.addItem(u, u)
        self.xscale.addItem("Linear", "linear")
        self.xscale.addItem("Logarithmic", "log")
        for text, val in (("Blank if available, else residuals", "auto"), ("Blank measurements", "blank"), ("Fit residuals", "residual")):
            self.sigma.addItem(text, val)
        self.k_lod = QDoubleSpinBox()
        self.k_lod.setRange(1, 10)
        self.k_lod.setDecimals(1)
        self.k_lod.setValue(float(prefs.get("lod_k")))
        self.k_lod.setToolTip("LOD = k·σ/S (3 = IUPAC-style, 3.3 = ICH); LOQ uses 10.")
        self.restrict = QCheckBox("Use only the linear range for S, LOD, LOQ")
        self.restrict.setChecked(True)
        self.r2 = QDoubleSpinBox()
        self.r2.setRange(0.5, 0.99999)
        self.r2.setDecimals(4)
        self.r2.setSingleStep(0.005)
        self.r2.setValue(0.99)
        self.dev = QDoubleSpinBox()
        self.dev.setRange(1, 50)
        self.dev.setDecimals(0)
        self.dev.setSuffix(" %")
        self.dev.setValue(10)
        self.time.setToolTip("Use one measurement session only: a calibration at day 0 and one at day 7 are different experiments.")
        for label, w in (("Quantity", self.quantity), ("Condition", self.condition), ("Electrode", self.group), ("Time", self.time), ("Model", self.model),
                         ("Concentration unit", self.unit), ("x axis", self.xscale), ("σ for LOD / LOQ", self.sigma),
                         ("LOD factor k", self.k_lod)):
            self.form.addRow(label, w)
        self.form.addRow(self.restrict)
        self.form.addRow("Linear range R² ≥", self.r2)
        self.form.addRow("Max deviation", self.dev)
        for w in (self.quantity, self.condition, self.group, self.time, self.model, self.unit, self.xscale, self.sigma):
            w.currentIndexChanged.connect(self._changed)
        self.quantity.currentIndexChanged.connect(self._refill_dependent)
        for w in (self.k_lod, self.r2, self.dev):
            w.setKeyboardTracking(False)
            w.valueChanged.connect(self._changed)
        self.restrict.toggled.connect(self._changed)
        self.samples_table = make_table()
        sc = Card("Samples (unknowns and spikes)", margins=10)
        sc.body.addWidget(self.samples_table, 1)
        self.side.insertWidget(self.side.count() - 1, sc)
        self.table.setToolTip("Model comparison: lower AIC is better.")
        self._loading = False
        self.refresh()

    def options(self) -> sa.CalibrationOptions:
        return sa.CalibrationOptions(
            quantity=self.quantity.currentText(), condition=self.condition.currentText() or "analyte",
            group=self.group.currentText() if self.group.currentIndex() > 0 else None, time=self.time.currentData(),
            model=self.model.currentData(),
            conc_unit=self.unit.currentData() or "", sigma_from=self.sigma.currentData(), k_lod=self.k_lod.value(),
            k_loq=self.k_lod.value() * 10 / 3, restrict_range=self.restrict.isChecked(), r2_min=self.r2.value(),
            dev_max=self.dev.value() / 100.0)

    def _refill_dependent(self) -> None:
        rs, q = self.session.responses, self.quantity.currentText()
        fill_combo(self.condition, sa.conditions(rs, q))
        fill_combo(self.group, sa.groups(rs, q), first=ALL_GROUPS)
        fill_time_combo(self.time, rs, q)

    def refresh(self) -> None:
        self._loading = True
        rs, st = self.session.responses, self.stored()
        fill_combo(self.quantity, sa.quantities(rs), keep=st.get("quantity"))
        self._refill_dependent()
        for cb, key in ((self.condition, "condition"), (self.group, "group")):
            if st.get(key):
                i = cb.findText(st[key])
                if i >= 0:
                    cb.setCurrentIndex(i)
        if "time" in st:
            fill_time_combo(self.time, rs, self.quantity.currentText(), keep=st["time"])
        for cb, key in ((self.model, "model"), (self.unit, "conc_unit"), (self.sigma, "sigma_from")):
            i = cb.findData(st.get(key, cb.currentData()))
            if i >= 0:
                with QSignalBlocker(cb):
                    cb.setCurrentIndex(i)
        for w, key, scale in ((self.k_lod, "k_lod", 1.0), (self.r2, "r2_min", 1.0), (self.dev, "dev_max", 100.0)):
            if key in st:
                with QSignalBlocker(w):
                    w.setValue(float(st[key]) * scale)
        if "restrict_range" in st:
            with QSignalBlocker(self.restrict):
                self.restrict.setChecked(bool(st["restrict_range"]))
        self._loading = False
        self.recompute()

    def _changed(self, *_a) -> None:
        if self._loading:
            return
        self.store(asdict(self.options()))
        self.recompute()

    def recompute(self) -> None:
        o = self.options()
        self.study = sa.run_calibration(self.session.responses, o) if o.quantity else None
        cs = self.study
        plots.study_calibration(self.plot.fig, cs, theme.current(), logx=self.xscale.currentData() == "log")
        self.plot.redraw()
        self.show_warnings(cs.warnings if cs else ["There are no responses yet: send some from the Analysis tab."])
        if cs is None or not cs.ok:
            self.set_kpis([("Sensitivity", "—", "", ""), ("LOD", "—", "", ""), ("Linear range", "—", "", ""), ("Model", "—", "", "")])
            self.table.setRowCount(0)
            self.samples_table.setRowCount(0)
            return
        f = cs.fom
        rng = f"{g(f.range_low, 3)} – {g(f.range_high, 3)}" if f and f.n_range else "—"
        r2 = cs.fit.r2
        self.set_kpis([
            ("Sensitivity", g(cs.sensitivity, 4), cs.sensitivity_unit, ""),
            ("LOD", g(cs.lod, 3), f"{cs.conc_unit} · LOQ {g(cs.loq, 3)}", ""),
            ("Linear range", rng, f"{cs.conc_unit} · {f.n_range if f else 0} levels", ""),
            ("Model", cal.MODEL_LABELS[cs.model].split(" ")[0], f"R² = {r2:.4f}", "success" if r2 >= 0.995 else ("warning" if r2 < 0.98 else "")),
        ])
        rows = []
        for k, fit in cs.fits.items():
            if fit.ok:
                rows.append([cal.MODEL_LABELS[k] + (" ✓" if k == cs.model else ""), f"{fit.r2:.4f}", g(fit.adj_r2, 4), g(fit.rmse, 3), g(fit.aic, 4),
                             ", ".join(f"{p.name}={g(p.value, 4)}" for p in fit.params)])
            else:
                rows.append([cal.MODEL_LABELS[k], "—", "—", "—", "—", fit.message])
        table_into(self.table, ["Model", "R²", "adj R²", "RMSE", "AIC", "Parameters"], rows)
        srows = [[s.label, g(s.response, 4), f"{g(s.conc, 4)} ± {g(s.se, 2)}" if math.isfinite(s.conc) else "outside range",
                  g(s.expected, 4), f"{s.recovery_pct:.1f} %" if math.isfinite(s.recovery_pct) else "—"] for s in cs.samples]
        table_into(self.samples_table, ["Sample", "Response", f"Found ({cs.conc_unit})", "Expected", "Recovery"], srows)


class ReproPanel(_Panel):
    key = "reproducibility"

    def __init__(self, session: Session):
        super().__init__(session)
        self.study: Optional[sa.ReproStudy] = None
        self.quantity, self.condition, self.time = QComboBox(), QComboBox(), QComboBox()
        self.form.addRow("Quantity", self.quantity)
        self.form.addRow("Condition", self.condition)
        self.form.addRow("Time", self.time)
        self.form.addRow(hint("Give each electrode its own Group name in the Responses table, and measure the same concentrations on each."))
        self.quantity.currentIndexChanged.connect(self._refill_dependent)
        for w in (self.quantity, self.condition, self.time):
            w.currentIndexChanged.connect(self._changed)
        self._loading = False
        self.refresh()

    def _refill_dependent(self) -> None:
        fill_combo(self.condition, sa.conditions(self.session.responses, self.quantity.currentText()))
        fill_time_combo(self.time, self.session.responses, self.quantity.currentText())

    def refresh(self) -> None:
        self._loading = True
        st = self.stored()
        fill_combo(self.quantity, sa.quantities(self.session.responses), keep=st.get("quantity"))
        fill_combo(self.condition, sa.conditions(self.session.responses, self.quantity.currentText()), keep=st.get("condition"))
        fill_time_combo(self.time, self.session.responses, self.quantity.currentText(), keep=st["time"] if "time" in st else None)
        self._loading = False
        self.recompute()

    def _changed(self, *_a) -> None:
        if not self._loading:
            self.store({"quantity": self.quantity.currentText(), "condition": self.condition.currentText(), "time": self.time.currentData()})
            self.recompute()

    def recompute(self) -> None:
        q = self.quantity.currentText()
        self.study = sa.run_reproducibility(self.session.responses, q, self.condition.currentText() or "analyte",
                                            time=self.time.currentData()) if q else None
        rs = self.study
        if rs:
            plots.study_reproducibility(self.plot.fig, rs, theme.current())
        else:
            self.plot.fig.clear()
        self.plot.redraw()
        self.show_warnings(rs.warnings if rs else [])
        if rs is None or not rs.rows:
            self.set_kpis([("Mean RSD", "—", "", ""), ("Electrodes", "—", "", ""), ("Sensitivity RSD", "—", "", "")])
            self.table.setRowCount(0)
            return
        rsds = [r.stats.rsd_pct for r in rs.rows if math.isfinite(r.stats.rsd_pct)]
        ss = rs.sensitivity_stats
        n_groups = max(r.n_groups for r in rs.rows)
        mean_rsd = sum(rsds) / len(rsds) if rsds else float("nan")
        self.set_kpis([("Mean RSD", f"{g(mean_rsd, 3)} %", "across concentration levels", "success" if mean_rsd < 5 else ("warning" if mean_rsd < 10 else "danger")) if rsds else ("Mean RSD", "—", "needs ≥ 2 electrodes", ""),
                       ("Electrodes", str(n_groups), "per level", ""),
                       ("Sensitivity RSD", f"{ss.rsd_pct:.3g} %" if ss and math.isfinite(ss.rsd_pct) else "—", "between electrodes", ""),
                       ("Mean sensitivity", g(ss.mean, 4) if ss else "—", f"{rs.y_unit}/{rs.conc_unit}", "")])
        table_into(self.table, ["Conc (" + rs.conc_unit + ")", "Electrodes", "Points", "Mean", "SD", "RSD %", "± CI 95 %", "Outliers"],
                   [[f"{r.conc:g}", str(r.n_groups), str(r.n_total), g(r.stats.mean, 4), g(r.stats.sd, 3), g(r.stats.rsd_pct, 3),
                     g(r.stats.ci95, 3), ", ".join(r.outliers) or "—"] for r in rs.rows])


class StabilityPanel(_Panel):
    key = "stability"

    def __init__(self, session: Session):
        super().__init__(session)
        self.study: Optional[sa.StabilityStudy] = None
        self.quantity, self.condition, self.conc, self.group, self.ref = (QComboBox() for _ in range(5))
        self.ref.addItem("First measurement", "first")
        self.ref.addItem("Highest response", "max")
        for label, w in (("Quantity", self.quantity), ("Condition", self.condition), ("Concentration", self.conc),
                         ("Electrode", self.group), ("Retention relative to", self.ref)):
            self.form.addRow(label, w)
        self.form.addRow(hint("Fill the Time column (days, hours, cycles…) in the Responses table: the same measurement repeated over time."))
        self.quantity.currentIndexChanged.connect(self._refill_dependent)
        for w in (self.quantity, self.condition, self.conc, self.group, self.ref):
            w.currentIndexChanged.connect(self._changed)
        self._loading = False
        self.refresh()

    def _refill_dependent(self) -> None:
        rs, q = self.session.responses, self.quantity.currentText()
        fill_combo(self.condition, sa.conditions(rs, q))
        fill_combo(self.group, sa.groups(rs, q), first=ALL_GROUPS)
        levels = sorted({f"{r.conc:g} {r.conc_unit}" for r in sa.usable(rs) if r.quantity == q and r.conc is not None and r.time is not None})
        fill_combo(self.conc, levels, first=ALL_CONC)

    def refresh(self) -> None:
        self._loading = True
        st = self.stored()
        fill_combo(self.quantity, sa.quantities(self.session.responses), keep=st.get("quantity"))
        self._refill_dependent()
        for cb, key in ((self.condition, "condition"), (self.conc, "conc"), (self.group, "group")):
            i = cb.findText(st.get(key, ""))
            if i >= 0:
                cb.setCurrentIndex(i)
        i = self.ref.findData(st.get("reference", "first"))
        if i >= 0:
            self.ref.setCurrentIndex(i)
        self._loading = False
        self.recompute()

    def _changed(self, *_a) -> None:
        if not self._loading:
            self.store({"quantity": self.quantity.currentText(), "condition": self.condition.currentText(),
                        "conc": self.conc.currentText() if self.conc.currentIndex() > 0 else "",
                        "group": self.group.currentText() if self.group.currentIndex() > 0 else "",
                        "reference": self.ref.currentData()})
            self.recompute()

    def recompute(self) -> None:
        q = self.quantity.currentText()
        if not q:
            self.study = None
            plots.study_stability(self.plot.fig, sa.StabilityStudy("", "", "", None), theme.current())
            self.plot.redraw()
            self.set_kpis([("Retention", "—", "", ""), ("Drift", "—", "", ""), ("Half-life", "—", "", ""), ("Time to 90 %", "—", "", "")])
            return
        conc, unit = None, ""
        if self.conc.currentIndex() > 0:
            txt = self.conc.currentText().split()
            conc, unit = float(txt[0]), txt[1]
        self.study = sa.run_stability(self.session.responses, q, self.condition.currentText() or "analyte", conc, unit or "",
                                      self.group.currentText() if self.group.currentIndex() > 0 else None, self.ref.currentData())
        st = self.study
        plots.study_stability(self.plot.fig, st, theme.current())
        self.plot.redraw()
        self.show_warnings(st.warnings)
        r = st.result
        if r is None:
            self.set_kpis([("Retention", "—", "", ""), ("Drift", "—", "", ""), ("Half-life", "—", "", ""), ("Time to 90 %", "—", "", "")])
            self.table.setRowCount(0)
            return
        ret = r.final_retention_pct
        self.set_kpis([("Retention", f"{ret:.1f} %", "at the last time point", "success" if ret >= 95 else ("warning" if ret >= 80 else "danger")),
                       ("Drift", g(r.drift_pct_per_unit, 3), "% per time unit", ""), ("Half-life", g(r.half_life, 3), "time units", ""),
                       ("Time to 90 %", g(r.t_90, 3), "time units (linear trend)", "")])
        table_into(self.table, ["Time", f"Response ({st.y_unit})", "Retention %"],
                   [[f"{t:g}", g(y, 5), f"{rp:.1f}"] for t, y, rp in zip(r.t, r.y, r.retention_pct)])


class SelectivityPanel(_Panel):
    key = "selectivity"

    def __init__(self, session: Session):
        super().__init__(session, n_kpis=2)
        self.study: Optional[sa.SelectivityStudy] = None
        self.quantity, self.analyte = QComboBox(), QComboBox()
        self.form.addRow("Quantity", self.quantity)
        self.form.addRow("Analyte condition", self.analyte)
        self.form.addRow(hint("Give interferents their own Condition name (e.g. “ascorbate”) in the Responses table, with their concentration. "
                              "Spike recovery is under Calibration → Samples."))
        for w in (self.quantity, self.analyte):
            w.currentIndexChanged.connect(self._changed)
        self.quantity.currentIndexChanged.connect(lambda _i: fill_combo(self.analyte, sa.conditions(self.session.responses, self.quantity.currentText())))
        self._loading = False
        self.refresh()

    def refresh(self) -> None:
        self._loading = True
        fill_combo(self.quantity, sa.quantities(self.session.responses), keep=self.stored().get("quantity"))
        stored = self.stored().get("analyte")
        fill_combo(self.analyte, sa.conditions(self.session.responses, self.quantity.currentText()),
                   keep=stored or ("analyte" if "analyte" in sa.conditions(self.session.responses) else None))
        self._loading = False
        self.recompute()

    def _changed(self, *_a) -> None:
        if not self._loading:
            self.store({"quantity": self.quantity.currentText(), "analyte": self.analyte.currentText()})
            self.recompute()

    def recompute(self) -> None:
        q = self.quantity.currentText()
        self.study = sa.run_selectivity(self.session.responses, q, self.analyte.currentText() or "analyte") if q else None
        ss = self.study
        if ss:
            plots.study_selectivity(self.plot.fig, ss, theme.current())
        else:
            self.plot.fig.clear()
        self.plot.redraw()
        self.show_warnings(ss.warnings if ss else [])
        if ss is None or not ss.rows:
            self.set_kpis([("Worst interferent", "—", "", ""), ("Interferents", "—", "", "")])
            self.table.setRowCount(0)
            return
        worst = max(ss.rows, key=lambda r: abs(r.change_pct) if math.isfinite(r.change_pct) else 0)
        self.set_kpis([("Worst interferent", worst.interferent, f"{worst.change_pct:+.1f} % vs analyte alone",
                        "success" if abs(worst.change_pct) <= 10 else "danger"), ("Interferents", str(len(ss.rows)), f"vs {ss.analyte} at {g(ss.analyte_conc, 4)} {ss.conc_unit}", "")])
        table_into(self.table, ["Interferent", f"Conc ({ss.conc_unit})", f"Response ({ss.y_unit})", "Change vs analyte %", "Interference % (equal conc)", "K (sensitivity ratio)"],
                   [[r.interferent, g(r.conc, 4), g(r.response, 4), f"{r.change_pct:+.1f}" if math.isfinite(r.change_pct) else "—",
                     g(r.interference_pct, 3), g(r.k_sel, 3)] for r in ss.rows])


class RecoveryPanel(_Panel):
    """Spike recovery: reads the calibration chosen on the Calibration panel."""
    key = "recovery"

    def __init__(self, session: Session, calibration: CalibrationPanel):
        super().__init__(session, n_kpis=2)
        self.calibration = calibration
        self.form.addRow(hint("Uses the calibration chosen on the Calibration tab. Mark a response's Role as “sample” and, for a spike, type the "
                              "concentration you added as its Conc: the recovery is found ÷ expected."))
        self.refresh()

    def refresh(self) -> None:
        self.recompute()

    def recompute(self) -> None:
        cs = self.calibration.study
        plots.study_recovery(self.plot.fig, cs, theme.current())
        self.plot.redraw()
        rows = [s for s in (cs.samples if cs else []) if math.isfinite(s.recovery_pct)]
        self.show_warnings([] if rows else ["No sample has both a measured response and an expected concentration."])
        if not rows:
            self.set_kpis([("Mean recovery", "—", "", ""), ("Samples", "—", "", "")])
            self.table.setRowCount(0)
            return
        rec = cs.recovery
        tone = "success" if 90 <= rec.mean_pct <= 110 else "warning"
        self.set_kpis([("Mean recovery", f"{rec.mean_pct:.1f} %", f"RSD {g(rec.rsd_pct, 3)} %", tone), ("Samples", str(len(rows)), "with an expected conc.", "")])
        table_into(self.table, ["Sample", f"Expected ({cs.conc_unit})", f"Found ({cs.conc_unit})", "Recovery %"],
                   [[s.label, g(s.expected, 4), f"{g(s.conc, 4)} ± {g(s.se, 2)}", f"{s.recovery_pct:.1f}"] for s in rows])


# =============================================================================== the tab

class StudyTab(QWidget):
    def __init__(self, session: Session, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.session = session
        self.responses = ResponsesPanel(session)
        self.calibration = CalibrationPanel(session)
        self.repro = ReproPanel(session)
        self.stability = StabilityPanel(session)
        self.selectivity = SelectivityPanel(session)
        self.recovery = RecoveryPanel(session, self.calibration)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("SubTabs")
        self.tabs.setDocumentMode(True)
        for w, name in ((self.responses, "Responses"), (self.calibration, "Calibration"), (self.repro, "Reproducibility"),
                        (self.stability, "Stability"), (self.selectivity, "Selectivity"), (self.recovery, "Recovery")):
            self.tabs.addTab(w, name)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.addWidget(self.tabs)
        self.tabs.currentChanged.connect(lambda _i: self.recovery.recompute())

    def refresh_all(self) -> None:
        for p in (self.calibration, self.repro, self.stability, self.selectivity):
            p.refresh()
        self.responses.rebuild()
        self.recovery.recompute()

    def get_state(self) -> dict:
        return {"sub": self.tabs.currentIndex()}

    def set_state(self, state: dict) -> None:
        self.refresh_all()
        sub = (state or {}).get("sub")
        if isinstance(sub, int) and 0 <= sub < self.tabs.count():
            self.tabs.setCurrentIndex(sub)
