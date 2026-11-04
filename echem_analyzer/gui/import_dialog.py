"""Import any table (CSV / TSV / text / Excel) as a measurement of a chosen technique."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import importers, techniques
from ..models import Curve, DataFile
from .ui_kit import Notice, hint

ROLE_LABELS = {"x": "X column", "y": "Y column", "freq": "Frequency column", "zre": "Z′ (real) column", "zim": "Z″ (imaginary) column"}


class ImportDialog(QDialog):
    """Pick the technique and the columns; the preview shows the first rows of the file."""

    def __init__(self, path: str | Path, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.path = Path(path)
        self.setWindowTitle(f"Import table: {self.path.name}")
        self.resize(760, 640)
        self.table: Optional[importers.Table] = None
        self.curve: Optional[Curve] = None
        lay = QVBoxLayout(self)
        self.notice = Notice()
        lay.addWidget(self.notice)
        self.preview = QTableWidget()
        self.preview.setFrameShape(QFrame.NoFrame)
        self.preview.verticalHeader().setVisible(False)
        self.preview.setEditTriggers(QTableWidget.NoEditTriggers)
        lay.addWidget(self.preview, 1)

        form = QFormLayout()
        self.tech = QComboBox()
        for k in techniques.ORDER:
            self.tech.addItem(f"{k} · {techniques.label(k)}", k)
        self.title = QLineEdit(self.path.stem)
        self.skip_auto = QCheckBox("Detect the header automatically")
        self.skip_auto.setChecked(True)
        self.skip = QSpinBox()
        self.skip.setRange(0, 10000)
        self.skip.setEnabled(False)
        self.skip.setPrefix("first data row ")
        self.negated = QCheckBox("The file lists −Z″ (positive values)")
        self.negated.setChecked(True)
        form.addRow("Technique", self.tech)
        form.addRow("Name", self.title)
        row = QHBoxLayout()
        row.addWidget(self.skip_auto)
        row.addWidget(self.skip)
        w = QWidget()
        w.setLayout(row)
        form.addRow("Header", w)
        self.role_boxes: dict[str, QComboBox] = {}
        self.role_form = QFormLayout()
        form.addRow(self.role_form)
        form.addRow(self.negated)
        lay.addLayout(form)
        lay.addWidget(hint("Units in the header text (V, mV, µA, nA, s, min, Hz, kHz, Ω, kΩ) are converted to V, µA, s, Hz, Ω."))
        self.box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.box.accepted.connect(self._accept)
        self.box.rejected.connect(self.reject)
        lay.addWidget(self.box)
        self.tech.currentIndexChanged.connect(self._technique_changed)
        self.skip_auto.toggled.connect(lambda on: (self.skip.setEnabled(not on), self._load()))
        self.skip.valueChanged.connect(lambda _v: self._load() if not self.skip_auto.isChecked() else None)
        self._load()

    # ------------------------------------------------------------------ loading and previewing
    def _load(self) -> None:
        try:
            self.table = importers.read_table(self.path, None if self.skip_auto.isChecked() else self.skip.value())
        except Exception as exc:  # noqa: BLE001 - show any parse problem in the dialog
            self.table = None
            self.notice.show_message(f"Could not read the file: {exc}", "warning")
            self.box.button(QDialogButtonBox.Ok).setEnabled(False)
            return
        self.box.button(QDialogButtonBox.Ok).setEnabled(True)
        t = self.table
        self.notice.show_message(f"{t.n_rows} rows × {len(t.columns)} numeric columns" + (f" · {', '.join(t.notes)}" if t.notes else ""), "neutral")
        n = min(12, t.n_rows)
        self.preview.setColumnCount(len(t.columns))
        self.preview.setHorizontalHeaderLabels([f"{c}\n[{u}]" if u else c for c, u in zip(t.columns, t.units)])
        self.preview.setRowCount(n)
        for i in range(n):
            for j in range(len(t.columns)):
                it = QTableWidgetItem(f"{t.data[i, j]:.6g}")
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.preview.setItem(i, j, it)
        self.preview.resizeColumnsToContents()
        # guess the technique from the file name / header, then the columns
        # An unrecognised table starts as generic x-y (always importable); the user then picks the technique.
        guess = techniques.classify(" ".join([self.path.stem] + t.columns))
        was_blocked = self.tech.blockSignals(True)
        self.tech.setCurrentIndex(max(self.tech.findData(guess), 0))
        self.tech.blockSignals(was_blocked)
        self._technique_changed()

    def _technique_changed(self) -> None:
        while self.role_form.rowCount():
            self.role_form.removeRow(0)
        self.role_boxes.clear()
        if self.table is None:
            return
        tech = self.tech.currentData()
        roles = importers.guess_roles(self.table, tech)
        for key in roles:
            cb = QComboBox()
            cb.addItem("— none —", None)
            for j, (c, u) in enumerate(zip(self.table.columns, self.table.units)):
                cb.addItem(f"{c}" + (f" [{u}]" if u else ""), j)
            i = cb.findData(roles[key])
            cb.setCurrentIndex(max(i, 0))
            self.role_form.addRow(ROLE_LABELS.get(key, key), cb)
            self.role_boxes[key] = cb
        self.negated.setVisible(tech == techniques.EIS)

    def roles(self) -> dict[str, Optional[int]]:
        return {k: cb.currentData() for k, cb in self.role_boxes.items()}

    # ------------------------------------------------------------------ result
    def _accept(self) -> None:
        try:
            self.curve = importers.build_curve(self.table, self.tech.currentData(), self.roles(), self.title.text().strip() or self.path.stem,
                                               eis_zim_negated=self.negated.isChecked())
        except ValueError as exc:
            self.notice.show_message(str(exc), "warning")
            return
        self.accept()

    def datafile(self) -> DataFile:
        return DataFile(path=self.path, fmt="table", curves=[self.curve])
