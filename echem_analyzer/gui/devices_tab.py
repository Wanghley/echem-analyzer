"""Devices: which electrode is which variant and lot, how it was made, whether it passes the acceptance rules, and the yield of a batch."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .. import acceptance as acc
from .. import devices as dv
from .. import theme
from ..presets import KIND_QC
from .session import Session
from .ui_kit import Card, Splitter, hint

FIXED = ["Electrode", "Variant", "Lot", "Fabricated", "Notes"]
C_ID, C_VARIANT, C_LOT, C_DATE, C_NOTES = range(5)
RULE_COLS = ["Quantity", "Min", "Max", "Condition", "Role", "Judged by", "Name"]
YIELD_COLS = ["Group", "Devices", "Passed", "Failed", "Not judged", "Yield", "95 % interval"]


def _table(headers: list[str], stretch_last: bool = True) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.horizontalHeader().setStretchLastSection(stretch_last)
    return t


def _num(text: str) -> Optional[float]:
    t = text.strip().replace(",", ".")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


class DevicesPanel(QWidget):
    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self._loading = False
        self._ids: list[str] = []
        self._params: list[str] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(8)

        bar = QHBoxLayout()
        bar.addWidget(hint("One row per electrode (the names used in the Group column of the Responses table). Give each a variant and a lot, and add "
                           "the fabrication parameters that differ between variants: results can then be compared across them."), 1)
        for text, fn in (("Add parameter…", self.add_parameter), ("Import CSV…", self.import_csv), ("Export CSV…", self.export_csv)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        lay.addLayout(bar)

        split = Splitter(Qt.Vertical)
        lay.addWidget(split, 1)
        dc = Card(margins=10)
        self.table = _table(FIXED, stretch_last=False)
        self.table.itemChanged.connect(self._device_edited)
        dc.body.addWidget(self.table, 1)
        split.addWidget(dc)

        low = Splitter(Qt.Horizontal)
        rc = Card("Acceptance rules", margins=10)
        self.rules = _table(RULE_COLS)
        self.rules.itemChanged.connect(self._rule_edited)
        rc.body.addWidget(self.rules, 1)
        rbar = QHBoxLayout()
        for text, fn in (("Add rule", self.add_rule), ("Remove selected", self.remove_rule), ("Save set…", self.save_set)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            rbar.addWidget(b)
        self.sets = QComboBox()
        self.sets.activated.connect(self._load_selected_set)
        rbar.addWidget(self.sets)
        rc.body.addLayout(rbar)
        rc.body.addWidget(hint("A device passes when every rule holds. “Judged by” says what to test when it has several measurements: mean, each "
                               "or last. Min and Max may be left empty."))
        low.addWidget(rc)
        yc = Card("Yield", margins=10)
        self.by = QComboBox()
        self.by.addItem("by variant", "variant")
        self.by.addItem("by lot", "lot")
        self.by.currentIndexChanged.connect(self._refresh_qc)
        yc.body.addWidget(self.by)
        self.yield_table = _table(YIELD_COLS)
        yc.body.addWidget(self.yield_table, 1)
        low.addWidget(yc)
        split.addWidget(low)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        session.responses_changed.connect(self.rebuild)
        session.files_changed.connect(self.rebuild)
        theme.bus.changed.connect(self._refresh_qc)
        self.rebuild()

    # ------------------------------------------------------------------ the registry
    def devices(self) -> dict[str, dv.Device]:
        return dv.sync(dv.load(self.session.extras), self.session.responses)

    def _store(self, devs: dict[str, dv.Device]) -> None:
        dv.save(self.session.extras, devs)
        self.session.touch()

    def rebuild(self) -> None:
        devs = self.devices()
        self._params = dv.param_names(devs)
        self._ids = list(devs)
        self._loading = True
        try:
            self.table.setColumnCount(len(FIXED) + len(self._params) + 1)
            self.table.setHorizontalHeaderLabels([*FIXED, *self._params, "Acceptance"])
            self.table.setRowCount(len(self._ids))
            for row, dev_id in enumerate(self._ids):
                d = devs[dev_id]
                values = [d.id, d.variant, d.lot, d.fabricated, d.notes,
                          *[("" if d.params.get(n, "") == "" else (f"{d.params[n]:g}" if dv.is_number(d.params[n]) else str(d.params[n]))) for n in self._params]]
                for col, text in enumerate(values):
                    it = QTableWidgetItem(text)
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | (Qt.NoItemFlags if col == C_ID else Qt.ItemIsEditable))
                    self.table.setItem(row, col, it)
                qc = QTableWidgetItem("")
                qc.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.table.setItem(row, self.table.columnCount() - 1, qc)
            self.table.resizeColumnsToContents()
        finally:
            self._loading = False
        self._fill_rules()
        self._fill_sets()
        self._refresh_qc()

    def _device_edited(self, it: QTableWidgetItem) -> None:
        if self._loading or it.column() == self.table.columnCount() - 1 or it.row() >= len(self._ids):
            return
        devs = self.devices()
        d = devs.get(self._ids[it.row()])
        if d is None:
            return
        text, col = it.text().strip(), it.column()
        if col == C_VARIANT:
            d.variant = text
        elif col == C_LOT:
            d.lot = text
        elif col == C_DATE:
            d.fabricated = text
        elif col == C_NOTES:
            d.notes = text
        else:
            d.params[self._params[col - len(FIXED)]] = dv.parse_value(text)
        self._store(devs)
        self._refresh_qc()

    def add_parameter(self) -> None:
        name, ok = QInputDialog.getText(self, "Add a fabrication parameter", "Name, with its unit (for example “ink volume (µL)”):")
        name = name.strip()
        if not ok or not name:
            return
        devs = self.devices()
        if name in dv.param_names(devs):
            return
        for d in devs.values():
            d.params[name] = ""
        self._store(devs)
        self.rebuild()

    def import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import devices", "", "Tables (*.csv *.tsv *.txt);;All files (*)")
        if not path:
            return
        try:
            raw = Path(path).read_bytes()
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
            incoming = dv.from_csv(text)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Import devices", str(exc))
            return
        self._store(dv.merge(self.devices(), incoming))
        self.rebuild()

    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export devices", "devices.csv", "CSV (*.csv)")
        if path:
            Path(path).write_text(dv.to_csv(self.devices()), encoding="utf-8")

    # ------------------------------------------------------------------ the rules
    def current_rules(self) -> list[acc.Rule]:
        return acc.load(self.session.extras)

    def _fill_rules(self) -> None:
        self._loading = True
        try:
            rules = self.current_rules()
            self.rules.setRowCount(len(rules))
            for row, r in enumerate(rules):
                cells = [r.quantity, "" if r.minimum is None else f"{r.minimum:g}", "" if r.maximum is None else f"{r.maximum:g}", r.condition,
                         r.role, r.how, r.name]
                for col, text in enumerate(cells):
                    self.rules.setItem(row, col, QTableWidgetItem(text))
            self.rules.resizeColumnsToContents()
        finally:
            self._loading = False

    def _rules_from_table(self) -> list[acc.Rule]:
        out = []
        for row in range(self.rules.rowCount()):
            cell = lambda c: (self.rules.item(row, c).text() if self.rules.item(row, c) else "").strip()          # noqa: E731
            if not cell(0):
                continue
            how = cell(5).lower()
            out.append(acc.Rule(cell(0), _num(cell(1)), _num(cell(2)), cell(3), cell(4), how if how in acc.HOW else "mean", cell(6)))
        return out

    def _rule_edited(self, _it: QTableWidgetItem) -> None:
        if self._loading:
            return
        rules = [acc.Rule.from_dict(r.to_dict()) for r in self._rules_from_table()]
        acc.save(self.session.extras, [r for r in rules if r is not None])
        self.session.touch()
        self._refresh_qc()

    def add_rule(self) -> None:
        rules = self.current_rules()
        rules.append(acc.Rule("Quantity"))
        acc.save(self.session.extras, rules)
        self.session.touch()
        self._fill_rules()
        self._refresh_qc()

    def remove_rule(self) -> None:
        rows = sorted({i.row() for i in self.rules.selectedIndexes()}, reverse=True)
        rules = self.current_rules()
        for r in rows:
            if r < len(rules):
                del rules[r]
        acc.save(self.session.extras, rules)
        self.session.touch()
        self._fill_rules()
        self._refresh_qc()

    def _fill_sets(self) -> None:
        self.sets.blockSignals(True)
        self.sets.clear()
        self.sets.addItem("Load a set…")
        for name in acc.TEMPLATES:
            self.sets.addItem(f"Template: {name}", ("template", name))
        for name in self.session.presets.names(KIND_QC):
            self.sets.addItem(f"Saved: {name}", ("saved", name))
        self.sets.blockSignals(False)

    def _load_selected_set(self, index: int) -> None:
        data = self.sets.itemData(index)
        if not data:
            return
        kind, name = data
        rules = acc.TEMPLATES[name] if kind == "template" else [r for r in (acc.Rule.from_dict(d) for d in
                                                                            (self.session.presets.get(KIND_QC, name) or {}).get("rules", [])) if r]
        self.load_rules(rules)

    def load_rules(self, rules: list[acc.Rule]) -> None:
        acc.save(self.session.extras, rules)
        self.session.touch()
        self._fill_rules()
        self._refresh_qc()

    def save_set(self) -> None:
        name, ok = QInputDialog.getText(self, "Save the rules", "Name of this set of acceptance rules:")
        if ok and name.strip():
            self.session.presets.save(KIND_QC, name.strip(), {"rules": [r.to_dict() for r in self.current_rules()]})
            self._fill_sets()

    # ------------------------------------------------------------------ verdicts and yield
    def results(self) -> list[acc.DeviceResult]:
        devs = self.devices()
        return acc.evaluate(self.session.responses, self.current_rules(), devs.keys())

    def _refresh_qc(self, *_a) -> None:
        if self._loading:
            return
        p = theme.current()
        results = {r.device: r for r in self.results()}
        last = self.table.columnCount() - 1
        self._loading = True
        try:
            for row, dev_id in enumerate(self._ids):
                r = results.get(dev_id)
                it = self.table.item(row, last)
                if it is None:
                    continue
                if r is None or r.status == acc.OVERALL_NONE:
                    text, color = "", p.muted
                elif r.status == acc.OVERALL_FAIL:
                    text, color = "fail: " + "; ".join(f"{x.rule.label()} (got {x.value:.4g})" for x in r.failed()), p.danger
                elif r.status == acc.OVERALL_INCOMPLETE:
                    missing = [x.rule.label() for x in r.results if x.status == acc.MISSING]
                    text, color = "not measured: " + "; ".join(missing), p.warning
                else:
                    text, color = "pass", p.success
                it.setText(text)
                it.setForeground(QColor(color))
            by = self.by.currentData()
            rows = acc.batch_yield(list(results.values()), self.devices(), by) if results and self.current_rules() else []
            self.yield_table.setRowCount(len(rows))
            for i, y in enumerate(rows):
                cells = [y.key, str(y.n), str(y.passed), str(y.failed), str(y.incomplete),
                         "—" if y.yield_pct != y.yield_pct else f"{y.yield_pct:.0f} %",
                         "—" if y.lo_pct != y.lo_pct else f"{y.lo_pct:.0f} – {y.hi_pct:.0f} %"]
                for c, text in enumerate(cells):
                    self.yield_table.setItem(i, c, QTableWidgetItem(text))
            self.yield_table.resizeColumnsToContents()
        finally:
            self._loading = False
