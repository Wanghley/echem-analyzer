"""Generic analysis workspace: any measurement, any applicable analyzer, results to the sensor study."""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import analyzers, theme
from ..analyzers import Analyzer, AnalysisResult, Field
from ..models import Curve
from .mpl_canvas import PlotWidget
from .session import Session, curve_run_label
from .ui_kit import Card, CollapsibleCard, KpiCard, Notice, Splitter, hint, persist_splitters


class AnalysisTab(QWidget):
    message = Signal(str)                 # status-bar text for the main window
    sent_to_study = Signal(int)           # how many responses were added

    def __init__(self, session: Session, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.session = session
        self.curve: Optional[Curve] = None
        self.analyzer: Optional[Analyzer] = None
        self.params: dict[str, Any] = {}
        self.result: Optional[AnalysisResult] = None
        self._widgets: dict[str, QWidget] = {}
        self._loading = False
        self._last_analyzer: dict[str, str] = {}
        self._build_ui()
        session.files_changed.connect(self.refresh_measurements)
        theme.bus.changed.connect(self._on_theme)
        self.refresh_measurements()

    # ================================================================== UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(8)
        for text, attr, stretch in (("MEASUREMENT", "meas_combo", 3), ("ANALYSIS", "an_combo", 1)):
            lab = QLabel(text)
            lab.setObjectName("CardTitle")
            top.addWidget(lab)
            cb = QComboBox()
            cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(24 if attr == "meas_combo" else 18)
            setattr(self, attr, cb)
            top.addWidget(cb, stretch)
        self.meas_combo.currentIndexChanged.connect(self._on_measurement)
        self.an_combo.currentIndexChanged.connect(self._on_analyzer)
        self.btn_send = QPushButton("Add to study")
        self.btn_send.setProperty("primary", True)
        self.btn_send.setToolTip("Send this analysis's numbers to the sensor study (one row per number).")
        self.btn_send.clicked.connect(self.send_to_study)
        self.btn_all = QPushButton("Add all of this technique")
        self.btn_all.setToolTip("Run this analysis on every measurement of the same technique and add them all to the study.")
        self.btn_all.clicked.connect(self.send_all)
        top.addWidget(self.btn_send)
        top.addWidget(self.btn_all)
        root.addLayout(top)

        split = Splitter(Qt.Horizontal)
        root.addWidget(split, 1)
        left = Splitter(Qt.Vertical)
        plot_card = Card(margins=8)
        self.plot = PlotWidget(figsize=(8, 4.2))
        plot_card.body.addWidget(self.plot, 1)
        self.about = hint("")
        plot_card.body.addWidget(self.about)
        left.addWidget(plot_card)
        self.table_card = Card("Table", margins=10)
        self.table = QTableWidget(0, 0)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table_card.body.addWidget(self.table, 1)
        left.addWidget(self.table_card)
        left.setStretchFactor(0, 4)
        left.setStretchFactor(1, 1)
        left.setSizes([620, 190])
        split.addWidget(left)

        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(2, 2, 10, 2)
        lay.setSpacing(10)
        self.kpis = [KpiCard("—") for _ in range(4)]
        grid = QGridLayout()
        grid.setSpacing(8)
        for k, w in enumerate(self.kpis):
            grid.addWidget(w, k // 2, k % 2)
        lay.addLayout(grid)
        self.notice = Notice()
        lay.addWidget(self.notice)
        card = Card("Results")
        self.results_label = QLabel()
        self.results_label.setTextFormat(Qt.RichText)
        self.results_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.results_label.setWordWrap(True)
        card.body.addWidget(self.results_label)
        lay.addWidget(card)
        self.form_card = Card("Settings")
        self.form = QFormLayout()
        self.form_card.body.addLayout(self.form)
        lay.addWidget(self.form_card)
        self.adv_card = CollapsibleCard("Advanced", expanded=False)
        self.adv_form = QFormLayout()
        self.adv_card.content_layout.addLayout(self.adv_form)
        lay.addWidget(self.adv_card)
        lay.addStretch(1)
        self.panel = panel
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(280)                       # the user can drag the grip to make it wider or narrower
        scroll.setFrameShape(QFrame.NoFrame)
        split.addWidget(scroll)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([1000, 372])

    # ================================================================== measurements and analyzers
    def refresh_measurements(self) -> None:
        cur = self.curve.uid if self.curve else None
        with QSignalBlocker(self.meas_combo):
            self.meas_combo.clear()
            for c in self.session.all_curves():
                self.meas_combo.addItem(f"[{c.kind}]  {curve_run_label(c)}", c.uid)
            i = self.meas_combo.findData(cur) if cur else -1
            self.meas_combo.setCurrentIndex(i if i >= 0 else (0 if self.meas_combo.count() else -1))
        has = self.meas_combo.count() > 0
        self.panel.setEnabled(has)
        for b in (self.btn_send, self.btn_all):
            b.setEnabled(has)
        if not has:
            self.curve = self.analyzer = self.result = None
            self.plot.fig.clear()
            self.plot.redraw()
            return
        self._load_curve(self.session.curve(self.meas_combo.currentData()))

    def select_measurement(self, uid: str) -> None:
        i = self.meas_combo.findData(uid)
        if i >= 0:
            self.meas_combo.setCurrentIndex(i)

    def _on_measurement(self, _i: int) -> None:
        c = self.session.curve(self.meas_combo.currentData())
        if c is not None:
            self._load_curve(c)

    def _load_curve(self, curve: Curve) -> None:
        self.curve = curve
        options = analyzers.for_kind(curve.kind)
        with QSignalBlocker(self.an_combo):
            self.an_combo.clear()
            for a in options:
                self.an_combo.addItem(a.title, a.key)
            want = self._last_analyzer.get(curve.kind)
            i = self.an_combo.findData(want) if want else 0
            self.an_combo.setCurrentIndex(max(i, 0))
        self._set_analyzer(analyzers.ANALYZERS[self.an_combo.currentData()])

    def _on_analyzer(self, _i: int) -> None:
        key = self.an_combo.currentData()
        if key and self.curve is not None:
            self._set_analyzer(analyzers.ANALYZERS[key])

    def _set_analyzer(self, a: Analyzer) -> None:
        self.analyzer = a
        if self.curve is not None:
            self._last_analyzer[self.curve.kind] = a.key
        self.about.setText(a.description)
        self._build_form(a)
        self.params = self.session.get_params(a.key, self.curve.uid)
        self._populate()
        self.plot.reset_toolbar()
        self.recompute()

    # ---- the settings form is built from the analyzer's declared fields
    def _clear_form(self, form: QFormLayout) -> None:
        while form.rowCount():
            form.removeRow(0)

    def _make_widget(self, f: Field) -> QWidget:
        if f.kind == "float":
            w = QDoubleSpinBox()
            w.setRange(f.lo, f.hi)
            w.setDecimals(f.decimals)
            w.setSingleStep(f.step)
            w.setKeyboardTracking(False)
            if f.suffix:
                w.setSuffix(f" {f.suffix}")
            w.valueChanged.connect(lambda _v, k=f.key: self._edited(k))
        elif f.kind == "int":
            w = QSpinBox()
            w.setRange(int(f.lo), int(f.hi))
            w.setSingleStep(int(max(f.step, 1)))
            w.setKeyboardTracking(False)
            if f.suffix:
                w.setSuffix(f" {f.suffix}")
            w.valueChanged.connect(lambda _v, k=f.key: self._edited(k))
        elif f.kind == "bool":
            w = QCheckBox(f.label)
            w.toggled.connect(lambda _v, k=f.key: self._edited(k))
        else:
            w = QComboBox()
            for text, val in f.choices:
                w.addItem(text, val)
            w.currentIndexChanged.connect(lambda _i, k=f.key: self._edited(k))
        if f.tip:
            w.setToolTip(f.tip)
        return w

    def _build_form(self, a: Analyzer) -> None:
        self._widgets.clear()
        self._clear_form(self.form)
        self._clear_form(self.adv_form)
        has_adv = False
        for f in a.fields:
            w = self._make_widget(f)
            self._widgets[f.key] = w
            form = self.adv_form if f.advanced else self.form
            has_adv = has_adv or f.advanced
            if f.kind == "bool":
                form.addRow(w)
            else:
                form.addRow(f.label, w)
        self.adv_card.setVisible(has_adv)
        self.form_card.setVisible(bool(a.fields))

    def _populate(self) -> None:
        self._loading = True
        try:
            for f in self.analyzer.fields:
                w, v = self._widgets[f.key], self.params[f.key]
                if isinstance(w, (QDoubleSpinBox, QSpinBox)):
                    w.setValue(v)
                elif isinstance(w, QCheckBox):
                    w.setChecked(bool(v))
                else:
                    i = w.findData(v)
                    w.setCurrentIndex(max(i, 0))
        finally:
            self._loading = False

    def _read(self) -> dict[str, Any]:
        out = {}
        for f in self.analyzer.fields:
            w = self._widgets[f.key]
            out[f.key] = (w.value() if isinstance(w, (QDoubleSpinBox, QSpinBox)) else
                          w.isChecked() if isinstance(w, QCheckBox) else w.currentData())
        return out

    def _edited(self, _key: str) -> None:
        if self._loading or self.analyzer is None or self.curve is None:
            return
        self.params = self._read()
        self.session.set_params(self.analyzer.key, self.curve.uid, self.params)
        self.recompute()

    # ================================================================== compute + render
    def recompute(self) -> None:
        if self.curve is None or self.analyzer is None:
            return
        self.result = self.analyzer.run(self.curve, self.params)
        self._draw()
        self._fill()

    def _on_theme(self) -> None:
        if self.result is not None:
            self._draw()
            self._fill()

    def _draw(self) -> None:
        self.analyzer.draw(self.plot.fig, self.curve, self.params, self.result, theme.current())
        self.plot.redraw()

    def _fill(self) -> None:
        r, p = self.result, theme.current()
        for k, card in enumerate(self.kpis):
            if r.ok and k < len(r.metrics):
                m = r.metrics[k]
                card.label.setText(m.label.upper())
                card.set(m.value, m.sub, m.tone)
                card.setVisible(True)
            else:
                card.setVisible(False)
        if r.warnings:
            self.notice.show_message("<br>".join("⚠ " + w for w in r.warnings), "warning")
        else:
            self.notice.setVisible(False)
        muted = f"color:{p.muted}"
        if r.ok and r.rows:
            parts = ["<table cellspacing='3'>"]
            for name, val, unit in r.rows:
                parts.append(f"<tr><td style='{muted}'>{name}</td><td align='right'><b>{val}</b></td><td style='{muted}'>&nbsp;{unit}</td></tr>")
            parts.append("</table>")
            if r.responses:
                parts.append(f"<p style='{muted}'>{len(r.responses)} number(s) can be sent to the study: "
                             + ", ".join(sorted({s.quantity for s in r.responses})) + ".</p>")
            self.results_label.setText("".join(parts))
        else:
            self.results_label.setText(f"<p style='{muted}'>No result yet. See the message above and the settings below.</p>")
        if r.table:
            header, rows = r.table
            self.table.setColumnCount(len(header))
            self.table.setHorizontalHeaderLabels(header)
            self.table.setRowCount(len(rows))
            for i, row in enumerate(rows):
                for j, v in enumerate(row):
                    it = QTableWidgetItem(str(v))
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter if j else Qt.AlignLeft | Qt.AlignVCenter)
                    self.table.setItem(i, j, it)
            self.table.resizeColumnsToContents()
            self.table_card.setVisible(True)
        else:
            self.table_card.setVisible(False)
        self.btn_send.setEnabled(bool(r.ok and r.responses))

    # ================================================================== send to the study
    def send_to_study(self) -> int:
        if self.result is None or not self.result.ok or self.curve is None:
            return 0
        rows = self.session.add_responses(self.curve, self.analyzer.key, self.result.responses)
        self.message.emit(f"Added {len(rows)} number(s) from {curve_run_label(self.curve)} to the study.")
        self.sent_to_study.emit(len(rows))
        return len(rows)

    def send_all(self) -> int:
        if self.curve is None or self.analyzer is None:
            return 0
        n_curves = n_rows = skipped = 0
        for c in self.session.all_curves():
            if c.kind != self.curve.kind or not self.analyzer.applies_to(c.kind):
                continue
            res = self.analyzer.run(c, self.session.get_params(self.analyzer.key, c.uid))
            if res.ok and res.responses:
                n_rows += len(self.session.add_responses(c, self.analyzer.key, res.responses))
                n_curves += 1
            else:
                skipped += 1
        self.message.emit(f"Added {n_rows} number(s) from {n_curves} measurement(s)"
                          + (f"; {skipped} could not be analysed with the current settings." if skipped else "."))
        self.sent_to_study.emit(n_rows)
        return n_rows

    # ================================================================== project state
    def get_state(self) -> dict:
        return {"uid": self.curve.uid if self.curve else None, "analyzer": self.analyzer.key if self.analyzer else None,
                "last": dict(self._last_analyzer)}

    def set_state(self, state: dict) -> None:
        state = state or {}
        self._last_analyzer.update(state.get("last") or {})
        if state.get("uid"):
            self.select_measurement(state["uid"])
        if state.get("analyzer"):
            i = self.an_combo.findData(state["analyzer"])
            if i >= 0:
                self.an_combo.setCurrentIndex(i)
