"""Overlay: plot any scans / curves from any loaded file together."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import plots, theme
from ..cv_analysis import analyze_cycle
from ..models import KIND_CA, KIND_CV, CVSettings, Curve
from ..reporting import g, write_overlay_csv
from .mpl_canvas import PlotWidget
from .ui_kit import Card, Splitter, hint, persist_splitters
from .session import Session, curve_run_label, curve_short_label, file_short

SKIP_FRAC = 0.05          # leading fraction of each curve ignored for the auto-zoom only

LINESTYLES = ["-", "--", ":", "-."]


class CVOverlayTab(QWidget):
    def __init__(self, session: Session, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.session = session
        self._seen: set[str] = set()
        self._checked: set[str] = set()
        self._build_ui()
        session.files_changed.connect(self.rebuild_tree)
        theme.bus.changed.connect(self.replot)
        self.rebuild_tree()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        split = Splitter(Qt.Horizontal)
        root.addWidget(split)

        left = Card("Curves to overlay")
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setFrameShape(QFrame.NoFrame)
        self.tree.itemChanged.connect(self._on_item_changed)
        left.body.addWidget(self.tree, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("Colour by"))
        self.color_combo = QComboBox()
        self.color_combo.addItem("Scan number", "scan")
        self.color_combo.addItem("File", "file")
        self.color_combo.currentIndexChanged.connect(lambda _i: self.replot())
        row.addWidget(self.color_combo, 1)
        left.body.addLayout(row)
        self.chk_zoom = QCheckBox("Zoom to features (ignore start-up transient)")
        self.chk_zoom.setChecked(True)
        self.chk_zoom.setToolTip(
            "The first points of a scan (especially scan 1) often show a large charging current\n"
            "that squashes everything else. When ticked, the y-axis ignores the first 5 % of each\n"
            "curve. Untick to see the full range."
        )
        self.chk_zoom.toggled.connect(lambda _v: self.replot())
        left.body.addWidget(self.chk_zoom)
        b = QPushButton("Export overlay as CSV…")
        b.clicked.connect(self.export_clicked)
        left.body.addWidget(b)
        split.addWidget(left)

        right = Splitter(Qt.Vertical)
        plot_card = Card(margins=8)
        self.plot = PlotWidget(figsize=(8, 5))
        self.ax = self.plot.fig.add_subplot(111)
        plot_card.body.addWidget(self.plot, 1)
        right.addWidget(plot_card)
        tbl_card = Card(margins=10)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["File", "Curve", "E pa (V)", "i pa (µA)", "E pc (V)", "i pc (µA)", "E½ (V)", "ΔEp (V)"]
        )
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setToolTip(
            "Anodic peak = maximum current on the positive-going sweep,\n"
            "cathodic peak = minimum current on the negative-going sweep.\n"
            "Use the Cycle analysis tab for windows, baselines and trends."
        )
        tbl_card.body.addWidget(self.table, 1)
        right.addWidget(tbl_card)
        right.setStretchFactor(0, 4)
        right.setStretchFactor(1, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([320, 1000])

    # ------------------------------------------------------------------ tree
    def rebuild_tree(self) -> None:
        # Only the very first file with CV/other curves is ticked automatically; later files
        # are left unticked so the overlay doesn't become unreadable.
        auto_check_available = not self._seen
        with QSignalBlocker(self.tree):
            self.tree.clear()
            for f in self.session.files:
                curves = [c for c in f.curves if c.kind != KIND_CA]
                if not curves:
                    continue
                top = QTreeWidgetItem([f.name])
                top.setFlags(top.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
                self.tree.addTopLevelItem(top)
                for c in curves:
                    if c.uid not in self._seen and auto_check_available:
                        self._checked.add(c.uid)
                    self._seen.add(c.uid)
                auto_check_available = False
                for c in curves:
                    it = QTreeWidgetItem([curve_short_label(c)])
                    it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                    it.setData(0, Qt.UserRole, c.uid)
                    it.setCheckState(0, Qt.Checked if c.uid in self._checked else Qt.Unchecked)
                    top.addChild(it)
                top.setExpanded(True)
        self.replot()

    def _on_item_changed(self, item: QTreeWidgetItem, _col: int) -> None:
        self.session.touch()
        uid = item.data(0, Qt.UserRole)
        if uid:
            (self._checked.add if item.checkState(0) == Qt.Checked else self._checked.discard)(uid)
        elif item.childCount():                 # a file row with auto-tristate: sync its children
            for j in range(item.childCount()):
                ch = item.child(j)
                u = ch.data(0, Qt.UserRole)
                (self._checked.add if ch.checkState(0) == Qt.Checked else self._checked.discard)(u)
        self.replot()

    # ---- project state
    def get_state(self) -> dict:
        return {"checked": sorted(self._checked), "color": self.color_combo.currentData(),
                "zoom": self.chk_zoom.isChecked()}

    def set_state(self, state: dict) -> None:
        state = state or {}
        self._checked = {u for u in state.get("checked", []) if self.session.curve(u) is not None}
        self._seen.update(c.uid for c in self.session.all_curves())      # saved choice wins over the auto-tick
        for widget, key in ((self.color_combo, "color"),):
            i = widget.findData(state.get(key))
            if i >= 0:
                widget.blockSignals(True)
                widget.setCurrentIndex(i)
                widget.blockSignals(False)
        self.chk_zoom.blockSignals(True)
        self.chk_zoom.setChecked(bool(state.get("zoom", True)))
        self.chk_zoom.blockSignals(False)
        self.rebuild_tree()

    def reset(self) -> None:
        """Forget which curves were ticked (called before a different project is loaded)."""
        self._seen.clear()
        self._checked.clear()

    def check_curve(self, uid: str) -> None:
        """Tick one curve in the tree (used when double-clicking it in the file dock)."""
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                if top.child(j).data(0, Qt.UserRole) == uid:
                    top.child(j).setCheckState(0, Qt.Checked)
                    return

    def checked_curves(self) -> list[Curve]:
        out = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                ch = top.child(j)
                if ch.checkState(0) == Qt.Checked:
                    c = self.session.curve(ch.data(0, Qt.UserRole))
                    if c is not None:
                        out.append(c)
        return out

    # ------------------------------------------------------------------ plot + table
    def replot(self) -> None:
        curves = self.checked_curves()
        files = [f.name for f in self.session.files]
        plots.cv_overlay(self.ax, curves, theme.current(), by_file=self.color_combo.currentData() == "file",
                         files=files, zoom=self.chk_zoom.isChecked(), label_fn=curve_run_label)
        self.plot.redraw()
        self._fill_table(curves)

    def _fill_table(self, curves: list[Curve]) -> None:
        rows = []
        for c in curves:
            if c.kind != KIND_CV:
                continue
            res = analyze_cycle(c.x, c.y, CVSettings(scan_rate=float(c.meta.get("scan_rate", 0) or 0)), cycle=c.scan or 0)
            rows.append((c, res))
        self.table.setRowCount(len(rows))
        for r, (c, p) in enumerate(rows):
            vals = [file_short(c.source), curve_short_label(c), g(p.e_pa, 4), g(p.i_pa, 4), g(p.e_pc, 4),
                    g(p.i_pc, 4), g(p.e_half, 4), g(p.delta_ep, 4)]
            for k, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if k >= 2:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, k, it)
        self.table.resizeColumnsToContents()

    # ------------------------------------------------------------------ export
    def export_csv(self, path: Path) -> int:
        curves = self.checked_curves()
        write_overlay_csv(path, curves)
        return len(curves)

    def export_clicked(self) -> None:
        if not self.checked_curves():
            QMessageBox.information(self, "Export overlay", "Tick at least one curve first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export overlay", "cv_overlay.csv", "CSV (*.csv)")
        if path:
            n = self.export_csv(Path(path))
            QMessageBox.information(self, "Export overlay", f"Wrote {n} curves to\n{path}")
