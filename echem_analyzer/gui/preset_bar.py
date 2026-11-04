"""A compact bar for saving and re-applying named analysis presets."""
from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QInputDialog, QMenu, QMessageBox, QPushButton, QToolButton, QVBoxLayout,
    QWidget,
)

from .. import presets as P
from .session import Session

SCOPE_RUN, SCOPE_ALL = "run", "all"


class PresetBar(QWidget):
    """Combo box of preset names + Apply / Save as… / Delete.

    ``kind`` is ``"ca"`` or ``"cv"``.  ``get_current`` returns the settings object of the run
    being shown (or ``None``); ``get_all`` returns the settings of every run of that kind.
    ``applied(scope)`` fires after a preset has been written into the settings, so the owner
    can refresh its controls and plots.
    """

    applied = Signal(str)

    def __init__(
        self,
        session: Session,
        kind: str,
        get_current: Callable[[], Optional[Any]],
        get_all: Callable[[], list],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.session, self.kind = session, kind
        self.get_current, self.get_all = get_current, get_all
        self._make = P.ca_preset_from_settings if kind == P.KIND_CA else P.cv_preset_from_settings
        self._apply = P.apply_ca_preset if kind == P.KIND_CA else P.apply_cv_preset
        self.last_ignored: list[str] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.combo = QComboBox()
        self.combo.setToolTip("Saved setups. They are stored in your user config folder, so every project can use them.")
        self.combo.setMinimumContentsLength(12)
        self.combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        lay.addWidget(self.combo)

        row = QHBoxLayout()
        row.setSpacing(4)
        self.btn_apply = QToolButton()
        self.btn_apply.setText("Apply")
        self.btn_apply.setPopupMode(QToolButton.MenuButtonPopup)
        menu = QMenu(self.btn_apply)
        a_run = QAction("Apply to this run", self)
        a_all = QAction("Apply to all runs", self)
        a_run.triggered.connect(lambda: self.apply_clicked(SCOPE_RUN))
        a_all.triggered.connect(lambda: self.apply_clicked(SCOPE_ALL))
        menu.addAction(a_run)
        menu.addAction(a_all)
        self.btn_apply.setMenu(menu)
        self.btn_apply.clicked.connect(lambda: self.apply_clicked(SCOPE_RUN))
        row.addWidget(self.btn_apply)

        self.btn_save = QPushButton("Save as…")
        self.btn_save.setToolTip("Save the current protocol (not the step times or excluded points) as a named preset.")
        self.btn_save.clicked.connect(self.save_clicked)
        self.btn_del = QPushButton("Delete")
        self.btn_del.clicked.connect(self.delete_clicked)
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_del)
        row.addStretch(1)
        lay.addLayout(row)
        self.refresh()

    # ------------------------------------------------------------------ core (no dialogs)
    def refresh(self, select: Optional[str] = None) -> None:
        keep = select or self.combo.currentText()
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItems(self.session.presets.names(self.kind))
        i = self.combo.findText(keep)
        self.combo.setCurrentIndex(i if i >= 0 else (0 if self.combo.count() else -1))
        self.combo.blockSignals(False)
        has = self.combo.count() > 0
        self.btn_apply.setEnabled(has)
        self.btn_del.setEnabled(has)

    def save_named(self, name: str) -> None:
        cur = self.get_current()
        if cur is None:
            raise ValueError("Nothing to save: open a run first.")
        self.session.presets.save(self.kind, name, self._make(cur))
        self.refresh(select=name.strip())

    def apply_named(self, name: str, scope: str = SCOPE_RUN) -> list[str]:
        values = self.session.presets.get(self.kind, name)
        if values is None:
            raise KeyError(name)
        targets = self.get_all() if scope == SCOPE_ALL else [self.get_current()]
        ignored: list[str] = []
        for t in targets:
            if t is not None:
                ignored += self._apply(t, values)
        self.last_ignored = sorted(set(ignored))
        self.session.touch()
        self.applied.emit(scope)
        return self.last_ignored

    def delete_named(self, name: str) -> bool:
        ok = self.session.presets.delete(self.kind, name)
        self.refresh()
        return ok

    # ------------------------------------------------------------------ buttons (with dialogs)
    def save_clicked(self) -> None:
        if self.get_current() is None:
            QMessageBox.information(self, "Save preset", "Open a run first.")
            return
        name, ok = QInputDialog.getText(self, "Save preset", "Name for this setup:", text=self.combo.currentText())
        name = name.strip()
        if not ok or not name:
            return
        if name in self.session.presets.names(self.kind) and QMessageBox.question(
            self, "Save preset", f"Replace the existing preset “{name}”?"
        ) != QMessageBox.Yes:
            return
        try:
            self.save_named(name)
        except OSError as exc:
            QMessageBox.critical(self, "Save preset", f"Could not write the presets file:\n{exc}")

    def apply_clicked(self, scope: str) -> None:
        name = self.combo.currentText()
        if not name:
            return
        if scope == SCOPE_ALL and QMessageBox.question(
            self, "Apply preset", f"Apply “{name}” to ALL runs? Their current protocol settings will be overwritten."
        ) != QMessageBox.Yes:
            return
        ignored = self.apply_named(name, scope)
        if ignored:
            QMessageBox.warning(self, "Preset partly applied",
                                "These entries in the preset were invalid and were skipped:\n" + ", ".join(ignored))

    def delete_clicked(self) -> None:
        name = self.combo.currentText()
        if name and QMessageBox.question(self, "Delete preset", f"Delete the preset “{name}”?") == QMessageBox.Yes:
            self.delete_named(name)
