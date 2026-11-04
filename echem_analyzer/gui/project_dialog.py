"""Dialog for the project's descriptive metadata."""
from __future__ import annotations

import copy
from typing import Optional

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget,
)

from ..project import ProjectMeta


class ProjectInfoDialog(QDialog):
    """Edit name, operator, electrode, analyte, sample matrix and notes of the project."""

    FIELDS = (("name", "Project name"), ("operator", "Operator"), ("electrode", "Electrode / sensor"),
              ("analyte", "Analyte"), ("matrix", "Sample matrix"))

    def __init__(self, meta: ProjectMeta, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Project info")
        self.setMinimumWidth(460)
        self._meta = copy.copy(meta)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.edits: dict[str, QLineEdit] = {}
        for key, label in self.FIELDS:
            e = QLineEdit(getattr(meta, key))
            self.edits[key] = e
            form.addRow(label, e)
        self.notes = QPlainTextEdit(meta.notes)
        self.notes.setMinimumHeight(110)
        form.addRow("Notes", self.notes)
        lay.addLayout(form)
        info = QLabel("This is saved in the project and written into the exported result tables.")
        info.setStyleSheet("color: #6b7280; font-size: 11px;")
        lay.addWidget(info)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def result_meta(self) -> ProjectMeta:
        m = copy.copy(self._meta)
        for key, _ in self.FIELDS:
            setattr(m, key, self.edits[key].text().strip())
        m.notes = self.notes.toPlainText().strip()
        return m
