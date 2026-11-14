"""Measurements: everything loaded, with its recognised technique (which you can correct) and a preview."""
from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .. import dataqc, techniques, theme
from ..models import Curve
from .mpl_canvas import PlotWidget
from .session import Session, curve_short_label, file_short
from .ui_kit import Card, Notice, Splitter, hint, persist_splitters

COLS = ["Technique", "Checks", "File", "Measurement", "Points", "x range", "y range", "Source"]
POINTS_COL = 4


class MeasurementsTab(QWidget):
    open_in_analysis = Signal(str)
    import_requested = Signal()

    def __init__(self, session: Session, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.session = session
        self._curves: list[Curve] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)
        bar = QHBoxLayout()
        bar.addWidget(hint("Every curve found in the loaded files. If a technique was recognised wrongly, correct it here; unusual files "
                           "can be brought in with Import table."), 1)
        self.btn_import = QPushButton("Import table…")
        self.btn_import.setToolTip("Bring in a CSV / TSV / text / Excel table from any instrument as a measurement of a chosen technique.")
        self.btn_import.clicked.connect(self.import_requested)
        self.btn_open = QPushButton("Analyse selected")
        self.btn_open.setProperty("primary", True)
        self.btn_open.clicked.connect(self._analyse)
        bar.addWidget(self.btn_import)
        bar.addWidget(self.btn_open)
        root.addLayout(bar)
        split = Splitter(Qt.Horizontal)
        root.addWidget(split, 1)
        card = Card(margins=10)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._preview)
        self.table.itemDoubleClicked.connect(lambda _i: self._analyse())
        card.body.addWidget(self.table, 1)
        split.addWidget(card)
        pc = Card("Preview", margins=8)
        self.plot = PlotWidget(figsize=(5, 4))
        pc.body.addWidget(self.plot, 1)
        self.checks_notice = Notice()
        pc.body.addWidget(self.checks_notice)
        pc.setMinimumWidth(380)
        split.addWidget(pc)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        session.files_changed.connect(self.rebuild)
        theme.bus.changed.connect(self._preview)
        theme.bus.changed.connect(self._color_checks)
        self.rebuild()

    def rebuild(self) -> None:
        sel = self.selected()
        self._curves = self.session.all_curves()
        flags = self.session.data_flags()
        self.table.setRowCount(len(self._curves))
        for i, c in enumerate(self._curves):
            cb = QComboBox()
            for k in techniques.ORDER:
                cb.addItem(k, k)
            cb.setCurrentIndex(max(cb.findData(c.kind), 0))
            cb.setToolTip(techniques.label(c.kind) + "\nChange it if the technique was recognised wrongly.")
            cb.currentIndexChanged.connect(lambda _i, uid=c.uid, box=cb: self.session.set_technique(uid, box.currentData()))
            self.table.setCellWidget(i, 0, cb)
            xr = f"{c.x.min():.4g} … {c.x.max():.4g} {c.x_unit}" if c.n else ""
            yr = f"{c.y.min():.4g} … {c.y.max():.4g} {c.y_unit}" if c.n else ""
            found = flags.get(c.uid, [])
            cells = (check_summary(found), file_short(c.source), curve_short_label(c), str(c.n), xr, yr,
                     "imported table" if c.meta.get("imported") else c.source)
            for j, text in enumerate(cells, start=1):
                it = QTableWidgetItem(text)
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                if j == POINTS_COL:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if j == 1:
                    it.setToolTip(dataqc.describe(found) if found else "The import checks found nothing to worry about.")
                    it.setData(Qt.UserRole, dataqc.worst(found))
                self.table.setItem(i, j, it)
        self._color_checks()
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(0, 92)
        self.table.doItemsLayout()                  # place the new combo boxes now, not on the next event-loop turn
        if sel is not None:
            for i, c in enumerate(self._curves):
                if c.uid == sel.uid:
                    self.table.selectRow(i)
                    break
        self._preview()

    def _color_checks(self) -> None:
        """Colour the Checks column by how serious the worst finding is (also after a theme change)."""
        p = theme.current()
        tone = {dataqc.ERROR: p.danger, dataqc.WARNING: p.warning, dataqc.INFO: p.muted, None: p.muted}
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 1)
            if it is not None:
                it.setForeground(QColor(tone.get(it.data(Qt.UserRole), p.muted)))

    def selected(self) -> Optional[Curve]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return self._curves[rows[0]] if rows and rows[0] < len(self._curves) else None

    def _preview(self) -> None:
        c, p = self.selected(), theme.current()
        fig = self.plot.fig
        fig.clear()
        ax = fig.add_subplot(111)
        if c is None:
            ax.set_axis_off()
            ax.text(0.5, 0.5, "Select a measurement", ha="center", va="center", transform=ax.transAxes, color=p.muted)
        else:
            if c.kind == techniques.EIS and "zre" in c.extra:
                ax.plot(c.extra["zre"], -c.extra["zim"], "o-", color=p.trace, ms=3, lw=1)
                ax.set_xlabel("Z′ (Ω)")
                ax.set_ylabel("−Z″ (Ω)")
                ax.set_aspect("equal", adjustable="datalim")
            else:
                ax.plot(c.x, c.y, color=p.trace, lw=1.2)
                ax.set_xlabel(c.x_unit or "x")
                ax.set_ylabel(c.y_unit or "y")
            ax.set_title(f"{c.kind} · {curve_short_label(c)}", loc="left", color=p.text)
        self.plot.redraw()
        self._show_checks(c)

    def _show_checks(self, c: Optional[Curve]) -> None:
        found = self.session.data_flags().get(c.uid, []) if c is not None else []
        if not found:
            self.checks_notice.setVisible(False)
            return
        mark = {dataqc.ERROR: "✖", dataqc.WARNING: "⚠", dataqc.INFO: "ℹ"}
        self.checks_notice.show_message("<br>".join(f"{mark[f.severity]} {f.message}" for f in found), "warning")

    def _analyse(self) -> None:
        c = self.selected()
        if c is not None:
            self.open_in_analysis.emit(c.uid)


def check_summary(flags) -> str:
    """'OK', '1 warning', '2 warnings' or '1 error, 1 warning': the Checks column's text."""
    if not flags:
        return "OK"
    parts = []
    for severity, word in ((dataqc.ERROR, "error"), (dataqc.WARNING, "warning"), (dataqc.INFO, "note")):
        n = sum(f.severity == severity for f in flags)
        if n:
            parts.append(f"{n} {word}{'s' if n != 1 else ''}")
    return ", ".join(parts)
