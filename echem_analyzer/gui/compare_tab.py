"""Compare CA runs / electrodes: overlaid traces, overlaid calibrations, summary table."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import plots, theme
from ..ca_analysis import analyze_ca
from ..models import CAAnalysisSettings, CAResult, Curve
from ..reporting import g
from .mpl_canvas import PlotWidget
from .ui_kit import Card, Splitter, hint, persist_splitters
from .session import Session, curve_run_label, curve_short_label, file_short

HEADERS = ["File", "Run", "Method", "Sensitivity", "per cm²", "R²", "LOD", "LOQ", "C_x", "C_sample", "Unit"]


class CompareTab(QWidget):
    def __init__(self, session: Session, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.session = session
        self._seen: set[str] = set()
        self._checked: set[str] = set()
        self._auto_keys: set[tuple] = set()
        self._rows: list[list[str]] = []
        self._build_ui()
        session.files_changed.connect(self.rebuild_list)
        session.analysis_changed.connect(lambda _uid: self.replot())
        theme.bus.changed.connect(self.replot)
        self.rebuild_list()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        split = Splitter(Qt.Horizontal)
        root.addWidget(split)

        left = Card("CA runs to compare")
        self.list = QListWidget()
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.itemChanged.connect(self._on_item_changed)
        left.body.addWidget(self.list, 1)
        self.chk_base = QCheckBox("Subtract baseline")
        self.chk_base.setChecked(True)
        self.chk_align = QCheckBox("Align time at first step")
        self.chk_align.setChecked(True)
        for c in (self.chk_base, self.chk_align):
            c.toggled.connect(lambda _v: self.replot())
            left.body.addWidget(c)
        left.body.addWidget(hint("Each run uses the settings (steps, volumes, method…) it has in the CA tab."))
        split.addWidget(left)

        right = Splitter(Qt.Vertical)
        plots_row = Splitter(Qt.Horizontal)
        c1, c2 = Card(margins=8), Card(margins=8)
        self.plot_ca = PlotWidget(figsize=(6, 4))
        self.ax_ca = self.plot_ca.fig.add_subplot(111)
        self.plot_cal = PlotWidget(figsize=(5, 4))
        self.ax_cal = self.plot_cal.fig.add_subplot(111)
        c1.body.addWidget(self.plot_ca, 1)
        c2.body.addWidget(self.plot_cal, 1)
        plots_row.addWidget(c1)
        plots_row.addWidget(c2)
        right.addWidget(plots_row)

        bottom = Card(margins=10)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(120)
        bottom.body.addWidget(self.table, 1)
        # the plots keep most of the height, but three runs must be readable without scrolling
        b = QPushButton("Export summary as CSV…")
        b.clicked.connect(self.export_clicked)
        row = QHBoxLayout()
        row.addWidget(b)
        row.addStretch(1)
        bottom.body.addLayout(row)
        right.addWidget(bottom)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 1)
        right.setSizes([640, 200])
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([300, 1100])

    # ------------------------------------------------------------------ list
    def rebuild_list(self) -> None:
        with QSignalBlocker(self.list):
            self.list.clear()
            for c in self.session.ca_curves():
                if c.uid not in self._seen:
                    self._seen.add(c.uid)
                    # Tick by default only runs that look like a standard-addition staircase (>= 2
                    # detected steps) and that are not an exact duplicate of one already ticked
                    # (the same run is often present in both the .csv and the .pssession).
                    key = (c.n, round(float(c.y[0]), 6), round(float(c.y[-1]), 6))
                    if len(self.session.get_settings(c).step_times) >= 2 and key not in self._auto_keys:
                        self._checked.add(c.uid)
                        self._auto_keys.add(key)
                it = QListWidgetItem(curve_run_label(c))
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setData(Qt.UserRole, c.uid)
                it.setCheckState(Qt.Checked if c.uid in self._checked else Qt.Unchecked)
                self.list.addItem(it)
        self.replot()

    # ---- project state
    def get_state(self) -> dict:
        return {"checked": sorted(self._checked), "subtract_baseline": self.chk_base.isChecked(),
                "align": self.chk_align.isChecked()}

    def set_state(self, state: dict) -> None:
        state = state or {}
        self._checked = {u for u in state.get("checked", []) if self.session.curve(u) is not None}
        self._seen.update(c.uid for c in self.session.ca_curves())      # the saved choice wins over auto-ticking
        for chk, key in ((self.chk_base, "subtract_baseline"), (self.chk_align, "align")):
            if key in state:
                with QSignalBlocker(chk):
                    chk.setChecked(bool(state[key]))
        self.rebuild_list()

    def reset(self) -> None:
        """Forget the selection (called before a different project is loaded)."""
        self._seen.clear()
        self._checked.clear()
        self._auto_keys.clear()

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        self.session.touch()
        uid = item.data(Qt.UserRole)
        (self._checked.add if item.checkState() == Qt.Checked else self._checked.discard)(uid)
        self.replot()

    def checked_curves(self) -> list[Curve]:
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.Checked:
                c = self.session.curve(it.data(Qt.UserRole))
                if c is not None:
                    out.append(c)
        return out

    # ------------------------------------------------------------------ plots + table
    def _analyses(self) -> list[tuple[Curve, CAAnalysisSettings, CAResult]]:
        out = []
        for c in self.checked_curves():
            s = self.session.get_settings(c)
            out.append((c, s, analyze_ca(c.x, c.y, s)))
        return out

    def replot(self) -> None:
        runs = self._analyses()
        sub_base, align = self.chk_base.isChecked(), self.chk_align.isChecked()
        p = theme.current()
        plots.compare_traces(self.ax_ca, runs, p, sub_base, align, curve_run_label)
        self.plot_ca.redraw()
        plots.compare_calibrations(self.ax_cal, runs, p, sub_base, curve_run_label)
        self.plot_cal.redraw()
        self._fill_table(runs)

    def _fill_table(self, runs) -> None:
        self._rows = []
        for c, s, r in runs:
            f = r.fit
            self._rows.append([
                file_short(c.source), curve_short_label(c), "drift-corrected" if s.method == "drift" else "plateau",
                g(r.sensitivity, 4) if f else "—",
                g(r.sensitivity_area, 4) if f and s.area_cm2 > 0 else "—",
                g(f.r2, 5) if f else "—", g(r.lod, 3) if f else "—", g(r.loq, 3) if f else "—",
                g(r.c_x, 4) if f else "—", g(r.c_sample, 4) if f and s.v0_ml > 0 else "—", s.stock_unit,
            ])
        self.table.setRowCount(len(self._rows))
        for i, row in enumerate(self._rows):
            for j, v in enumerate(row):
                it = QTableWidgetItem(v)
                if j >= 3:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, j, it)
        self.table.resizeColumnsToContents()

    # ------------------------------------------------------------------ export
    def export_csv(self, path: Path) -> int:
        with Path(path).open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(HEADERS)
            w.writerows(self._rows)
        return len(self._rows)

    def export_clicked(self) -> None:
        if not self._rows:
            QMessageBox.information(self, "Export summary", "Tick at least one CA run first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export summary", "ca_comparison.csv", "CSV (*.csv)")
        if path:
            n = self.export_csv(Path(path))
            QMessageBox.information(self, "Export summary", f"Wrote {n} rows to\n{path}")
