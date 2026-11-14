"""Bench calculators: dilutions, standard-addition volumes, expected signal, Nernst slope, ionic strength, weighing.

Every page recomputes as you type.  While an input is missing or cannot be right the page says so in plain words instead of
showing a number.  The arithmetic is in :mod:`echem_analyzer.bench`; this module only collects inputs and shows results.
"""
from __future__ import annotations

import math
import re
from typing import Optional

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from .. import bench
from ..bench import BenchError
from .ui_kit import Notice, hint

CONC_UNITS = ("M", "mM", "µM", "nM", "pM")
VOLUME_UNITS = ("µL", "mL", "L")


def parse_number(text: str, name: str, required: bool = True) -> Optional[float]:
    """A number from a text box ("1.5", "1,5", "2e-6"); None when optional and blank; a plain-language error otherwise."""
    t = text.strip().replace(",", ".")
    if not t:
        if required:
            raise BenchError(f"Enter {name}.")
        return None
    try:
        value = float(t)
    except ValueError:
        raise BenchError(f"{name} is not a number: “{text.strip()}”.") from None
    if not math.isfinite(value):
        raise BenchError(f"{name} is not a number: “{text.strip()}”.")
    return value


def parse_list(text: str, name: str) -> list[float]:
    """Numbers separated by commas, semicolons or spaces ("10, 20, 50" or "10 20 50"; a decimal comma needs spaces or semicolons)."""
    t = text.strip()
    if not t:
        raise BenchError(f"Enter {name}.")
    parts = re.split(r"[;\s]+", t) if re.search(r"[;\s]", t) else t.split(",")
    return [parse_number(p, name) for p in (p.strip(",") for p in parts) if p]


def g(value: float, digits: int = 4) -> str:
    return f"{value:.{digits}g}"


def _edit(text: str = "", placeholder: str = "", width: int = 110) -> QLineEdit:
    e = QLineEdit(text)
    e.setPlaceholderText(placeholder)
    e.setFixedWidth(width)
    return e


def _combo(items, current: Optional[str] = None) -> QComboBox:
    c = QComboBox()
    c.addItems(list(items))
    if current:
        c.setCurrentText(current)
    return c


def _row(*widgets: QWidget) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for x in widgets:
        lay.addWidget(x)
    lay.addStretch(1)
    return w


class _Calc(QWidget):
    """A calculator page: an explanation, inputs, a result message and (optionally) a table."""

    def __init__(self, intro: str, table_headers: Optional[list[str]] = None):
        super().__init__()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(14, 12, 14, 12)
        self.lay.setSpacing(10)
        self.lay.addWidget(hint(intro))
        self.form = QFormLayout()
        self.lay.addLayout(self.form)
        self.result = Notice()
        self.lay.addWidget(self.result)
        self.table: Optional[QTableWidget] = None
        if table_headers:
            self.table = QTableWidget(0, len(table_headers))
            self.table.setHorizontalHeaderLabels(table_headers)
            self.table.verticalHeader().setVisible(False)
            self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.table.setAlternatingRowColors(True)
            self.table.horizontalHeader().setStretchLastSection(True)
            self.lay.addWidget(self.table, 1)
        else:
            self.lay.addStretch(1)

    def watch(self, *widgets: QWidget) -> None:
        for w in widgets:
            if isinstance(w, QLineEdit):
                w.textChanged.connect(self.refresh)
            elif isinstance(w, QComboBox):
                w.currentIndexChanged.connect(self.refresh)
            elif isinstance(w, QSpinBox):
                w.valueChanged.connect(self.refresh)

    def refresh(self, *_args) -> None:
        try:
            self.compute()
        except BenchError as exc:
            self.problem(str(exc))

    def compute(self) -> None:                       # pragma: no cover - overridden
        raise NotImplementedError

    def problem(self, text: str) -> None:
        if self.table is not None:
            self.table.setRowCount(0)
        self.result.show_message(text, "neutral")

    def answer(self, html: str, tone: str = "success") -> None:
        self.result.show_message(html, tone)

    def fill_table(self, rows: list[list[str]]) -> None:
        assert self.table is not None
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, text in enumerate(row):
                self.table.setItem(i, j, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()

    def table_rows(self) -> list[list[str]]:
        if self.table is None:
            return []
        return [[self.table.item(r, c).text() for c in range(self.table.columnCount())] for r in range(self.table.rowCount())]


# --------------------------------------------------------------------------- the pages

class DilutionPage(_Calc):
    TITLE = "Dilution"

    def __init__(self):
        super().__init__("C₁·V₁ = C₂·V₂. Fill in any three boxes and the fourth is calculated. Both concentrations share one "
                         "unit, both volumes another.")
        self.c1, self.v1 = _edit(placeholder="calculated"), _edit(placeholder="calculated")
        self.c2, self.v2 = _edit(placeholder="calculated"), _edit(placeholder="calculated")
        self.cu, self.vu = _combo(CONC_UNITS, "mM"), _combo(VOLUME_UNITS, "mL")
        self.form.addRow("Starting concentration C₁", _row(self.c1, self.cu))
        self.form.addRow("Volume taken V₁", _row(self.v1, self.vu))
        self.form.addRow("Final concentration C₂", self.c2)
        self.form.addRow("Final volume V₂", self.v2)
        self.watch(self.c1, self.v1, self.c2, self.v2, self.cu, self.vu)
        self.refresh()

    def compute(self) -> None:
        boxes = {"c1": self.c1, "v1": self.v1, "c2": self.c2, "v2": self.v2}
        names = {"c1": "the starting concentration", "v1": "the volume taken", "c2": "the final concentration", "v2": "the final volume"}
        values = {k: parse_number(e.text(), names[k], required=False) for k, e in boxes.items()}
        empty = [k for k, v in values.items() if v is None]
        if len(empty) != 1:
            raise BenchError("Fill in three of the four boxes; the empty one is calculated.")
        d = bench.solve_dilution(**values)
        cu, vu = self.cu.currentText(), self.vu.currentText()
        self.answer(f"Take <b>{g(d.v1)} {vu}</b> of the {g(d.c1)} {cu} solution and add <b>{g(d.diluent)} {vu}</b> of solvent: "
                    f"{g(d.v2)} {vu} at {g(d.c2)} {cu} (a {g(d.factor)}× dilution).")


class SerialPage(_Calc):
    TITLE = "Serial dilution"

    def __init__(self):
        super().__init__("A row of tubes, each diluted from the one before by the same factor. Every tube ends at the volume you give.",
                         ["Tube", "Concentration", "Move in", "Solvent already in the tube"])
        self.stock, self.unit = _edit("10"), _combo(CONC_UNITS, "mM")
        self.factor, self.tubes = _edit("10"), QSpinBox()
        self.tubes.setRange(1, 30)
        self.tubes.setValue(6)
        self.vol, self.vu = _edit("1000"), _combo(("µL", "mL"), "µL")
        self.form.addRow("Stock concentration", _row(self.stock, self.unit))
        self.form.addRow("Dilution factor (10 = tenfold)", self.factor)
        self.form.addRow("Number of tubes", self.tubes)
        self.form.addRow("Volume in each tube", _row(self.vol, self.vu))
        self.watch(self.stock, self.unit, self.factor, self.tubes, self.vol, self.vu)
        self.refresh()

    def compute(self) -> None:
        rows = bench.serial_dilution(parse_number(self.stock.text(), "the stock concentration"),
                                     parse_number(self.factor.text(), "the dilution factor"), self.tubes.value(),
                                     parse_number(self.vol.text(), "the volume per tube"))
        cu, vu = self.unit.currentText(), self.vu.currentText()
        self.fill_table([[str(r.tube), f"{g(r.conc)} {cu}", f"{g(r.take)} {vu}", f"{g(r.diluent)} {vu}"] for r in rows])
        self.answer(f"Tube 1 takes {g(rows[0].take)} {vu} of the stock; every later tube takes {g(rows[0].take)} {vu} from the one before. "
                    f"The last tube is {g(rows[-1].conc)} {cu}.")


class AdditionPage(_Calc):
    TITLE = "Standard addition"
    ROUNDING = {"exact": 0.0, "0.1 µL": 0.1, "0.5 µL": 0.5, "1 µL": 1.0, "5 µL": 5.0}

    def __init__(self):
        super().__init__("Volumes of stock to pipette into the cell so that its concentration reaches each target in turn. The stock is "
                         "diluted as it is added, so the later volumes are slightly larger than a straight proportion.",
                         ["Step", "Target", "Add now (µL)", "Total added (µL)", "Concentration reached", "Original solution left"])
        self.stock, self.unit = _edit("10000"), _combo(CONC_UNITS, "µM")          # a 10 mM stock, shown in the unit of the targets
        self.cell = _edit("10")
        self.targets = QLineEdit("10, 20, 50, 100")
        self.targets.setToolTip("Concentrations wanted in the cell, in the unit of the stock. Separate with commas or spaces.")
        self.target_unit = QLabel()
        self.round = _combo(self.ROUNDING, "0.1 µL")
        self.min_pipette = _edit("0.5", width=70)
        self.form.addRow("Stock concentration", _row(self.stock, self.unit))
        self.form.addRow("Cell volume (mL)", self.cell)
        self.form.addRow("Target concentrations", _row(self.targets, self.target_unit))
        self.form.addRow("Round volumes to", self.round)
        self.form.addRow("Smallest pipette volume (µL)", self.min_pipette)
        self.copy_btn = QPushButton("Copy table")
        self.copy_btn.clicked.connect(self.copy_table)
        self.lay.insertWidget(self.lay.count() - 1, _row(self.copy_btn))
        self.watch(self.stock, self.unit, self.cell, self.targets, self.round, self.min_pipette)
        self.refresh()

    def compute(self) -> None:
        unit = self.unit.currentText()
        self.target_unit.setText(unit)
        stock = parse_number(self.stock.text(), "the stock concentration")
        plan = bench.addition_plan(stock, parse_number(self.cell.text(), "the cell volume"),
                                   parse_list(self.targets.text(), "the target concentrations"),
                                   round_to_ul=self.ROUNDING[self.round.currentText()],
                                   min_pipette_ul=parse_number(self.min_pipette.text(), "the smallest pipette volume"))
        self.fill_table([[str(s.step), f"{g(s.target)} {unit}", f"{s.add_ul:.2f}", f"{s.cumulative_ul:.2f}",
                          f"{g(s.actual)} {unit}", f"{s.matrix_pct:.1f} %"] for s in plan.steps])
        lines = [f"Total {plan.total_ul:.1f} µL = {plan.total_pct_of_cell:.1f} % of the cell. "
                 f"A stock of {g(bench.min_stock_for(plan.steps[-1].target))} {unit} or more keeps the additions under 5 %."]
        lines += [f"⚠ {w}" for w in plan.warnings]
        self.answer("<br>".join(lines), "warning" if plan.warnings else "success")

    def copy_table(self) -> None:
        text = "\n".join("\t".join(r) for r in [["Step", "Target", "Add (µL)", "Total (µL)", "Reached", "Left"], *self.table_rows()])
        QGuiApplication.clipboard().setText(text)


class SignalPage(_Calc):
    TITLE = "Expected signal"

    def __init__(self):
        super().__init__("Peak current to expect from a diffusion-controlled redox couple (Randles–Sevcik), and the instrument current range "
                         "that holds it with room to spare. Too small a range clips the peak; too large a one loses resolution.",
                         ["Scan rate", "Peak current", "Range to set"])
        self.n = QSpinBox()
        self.n.setRange(1, 6)
        self.n.setValue(1)
        self.area, self.conc = _edit("0.0707"), _edit("1")
        self.d, self.rate, self.temp = _edit("7.6e-6"), _edit("100"), _edit("25", width=70)
        self.alpha = _edit(placeholder="blank = reversible")
        self.btn = QPushButton("Typical: 1 mM ferricyanide, 3 mm disk")
        self.btn.clicked.connect(self.typical)
        self.form.addRow("Electrons transferred n", self.n)
        self.form.addRow("Electrode area (cm²)", self.area)
        self.form.addRow("Concentration (mM)", self.conc)
        self.form.addRow("Diffusion coefficient (cm²/s)", self.d)
        self.form.addRow("Scan rate (mV/s)", self.rate)
        self.form.addRow("Temperature (°C)", self.temp)
        self.form.addRow("α·nₐ if irreversible", self.alpha)
        self.form.addRow("", self.btn)
        self.watch(self.n, self.area, self.conc, self.d, self.rate, self.temp, self.alpha)
        self.refresh()

    def typical(self) -> None:
        for edit, text in ((self.area, "0.0707"), (self.conc, "1"), (self.d, "7.6e-6"), (self.rate, "100"), (self.temp, "25"), (self.alpha, "")):
            edit.setText(text)
        self.n.setValue(1)

    def _ip(self, rate_mv_s: float) -> float:
        return bench.expected_peak_current(self.n.value(), parse_number(self.area.text(), "the electrode area"),
                                           parse_number(self.conc.text(), "the concentration"),
                                           parse_number(self.d.text(), "the diffusion coefficient"), rate_mv_s / 1000.0,
                                           parse_number(self.temp.text(), "the temperature"),
                                           parse_number(self.alpha.text(), "α·nₐ", required=False))

    def compute(self) -> None:
        rate = parse_number(self.rate.text(), "the scan rate")
        ip = self._ip(rate)
        advice = bench.current_range(ip)
        rates = sorted({10, 20, 50, 100, 200, 500, 1000, rate})
        self.fill_table([[f"{g(r)} mV/s", f"{g(self._ip(r))} µA", bench.current_range(self._ip(r)).label] for r in rates])
        widest = bench.current_range(max(self._ip(r) for r in rates))
        self.answer(f"At {g(rate)} mV/s expect a peak of about <b>{g(ip)} µA</b>: set the current range to "
                    f"<b>{advice.label}</b> (the peak fills {advice.fill_pct:.0f} % of it). To cover every scan rate listed, use "
                    f"<b>{widest.label}</b>. Charging current and a larger-than-predicted peak are what the headroom is for.")


class NernstPage(_Calc):
    TITLE = "Nernst slope"

    def __init__(self):
        super().__init__("Ideal response of an ion-selective electrode: 2.303·R·T/(z·F) per decade of activity. Real electrodes are "
                         "judged against it (a slope of 50–58 mV/decade for a monovalent ion is typical of a good one).")
        self.z = QSpinBox()
        self.z.setRange(-4, 4)
        self.z.setValue(1)
        self.temp = _edit("25", width=70)
        self.low, self.high = _edit("0.1"), _edit("10")
        self.unit = _combo(CONC_UNITS, "mM")
        self.form.addRow("Charge z (cation +, anion −)", self.z)
        self.form.addRow("Temperature (°C)", self.temp)
        self.form.addRow("From concentration", _row(self.low, self.unit))
        self.form.addRow("To concentration", self.high)
        self.watch(self.z, self.temp, self.low, self.high, self.unit)
        self.refresh()

    def compute(self) -> None:
        p = bench.nernst_prediction(self.z.value(), parse_number(self.temp.text(), "the temperature"),
                                    parse_number(self.low.text(), "the lower concentration"),
                                    parse_number(self.high.text(), "the higher concentration"))
        direction = "rises" if p.delta_mv > 0 else "falls"
        self.answer(f"Slope <b>{p.slope_mv_per_decade:+.2f} mV/decade</b>. Over {g(p.decades)} decade(s) the potential {direction} by "
                    f"<b>{abs(p.delta_mv):.1f} mV</b>. The slope itself changes by {p.slope_change_per_kelvin:+.3f} mV/decade per kelvin, "
                    "so a sensor that is not held at the calibration temperature needs compensating.")


class IonicStrengthPage(_Calc):
    TITLE = "Ionic strength"
    PBS = [("NaCl", "137", 1, 1), ("KCl", "2.7", 1, 1), ("Na₂HPO₄", "10", 1, 2), ("KH₂PO₄", "1.8", 1, 1)]

    def __init__(self):
        super().__init__("Ionic strength I = ½ Σ cᵢzᵢ² of the solution, and the Debye length: how far a charge on a sensor surface is "
                         "felt. A field-effect or label-free sensor sees binding only within a few Debye lengths. Salts are taken "
                         "as fully dissociated.")
        self.salts = QTableWidget(0, 4)
        self.salts.setHorizontalHeaderLabels(["Salt (name optional)", "Concentration (mM)", "Cation charge", "Anion charge"])
        self.salts.verticalHeader().setVisible(False)
        self.salts.horizontalHeader().setStretchLastSection(True)
        self.salts.setMinimumHeight(150)
        self.lay.insertWidget(2, self.salts)
        self.temp = _edit("25", width=70)
        self.form.addRow("Temperature (°C)", self.temp)
        add, remove, pbs = QPushButton("Add salt"), QPushButton("Remove selected"), QPushButton("1× PBS")
        add.clicked.connect(lambda: self.add_salt())
        remove.clicked.connect(self.remove_selected)
        pbs.setToolTip("NaCl 137 mM, KCl 2.7 mM, Na₂HPO₄ 10 mM, KH₂PO₄ 1.8 mM (phosphate treated as fully dissociated).")
        pbs.clicked.connect(self.load_pbs)
        self.lay.insertWidget(3, _row(add, remove, pbs))
        self.salts.itemChanged.connect(self.refresh)
        self.temp.textChanged.connect(self.refresh)
        self.add_salt("NaCl", "100", 1, 1)

    def add_salt(self, name: str = "", conc: str = "", zc: int = 1, za: int = 1) -> None:
        self.salts.blockSignals(True)
        r = self.salts.rowCount()
        self.salts.insertRow(r)
        for c, text in enumerate((name, conc, str(zc), str(za))):
            self.salts.setItem(r, c, QTableWidgetItem(text))
        self.salts.blockSignals(False)
        self.refresh()

    def remove_selected(self) -> None:
        for r in sorted({i.row() for i in self.salts.selectedIndexes()}, reverse=True):
            self.salts.removeRow(r)
        self.refresh()

    def load_pbs(self) -> None:
        self.salts.setRowCount(0)
        for entry in self.PBS:
            self.add_salt(*entry)

    def compute(self) -> None:
        species: list[tuple[float, int]] = []
        for r in range(self.salts.rowCount()):
            cells = [self.salts.item(r, c).text() if self.salts.item(r, c) else "" for c in range(4)]
            if not cells[1].strip():
                continue
            conc_mm = parse_number(cells[1], f"the concentration in row {r + 1}")
            zc = parse_number(cells[2], f"the cation charge in row {r + 1}")
            za = parse_number(cells[3], f"the anion charge in row {r + 1}")
            species += bench.salt_species(conc_mm / 1000.0, int(zc), int(za))
        if not species:
            raise BenchError("Add at least one salt with a concentration.")
        i = bench.ionic_strength(species)
        t = parse_number(self.temp.text(), "the temperature")
        self.answer(f"Ionic strength <b>{g(i * 1000)} mM</b>. Debye length <b>{g(bench.debye_length_nm(i, t))} nm</b> at {g(t)} °C. "
                    "Diluting the sample tenfold lengthens it about threefold.")


class WeighPage(_Calc):
    TITLE = "Weighing"

    def __init__(self):
        super().__init__("Mass of a compound to dissolve to make a solution of a given concentration and volume.")
        self.conc, self.unit = _edit("50"), _combo(CONC_UNITS, "mM")
        self.vol, self.mw = _edit("10"), _edit("329.24")
        self.form.addRow("Concentration", _row(self.conc, self.unit))
        self.form.addRow("Volume (mL)", self.vol)
        self.form.addRow("Molar mass (g/mol)", self.mw)
        self.watch(self.conc, self.unit, self.vol, self.mw)
        self.refresh()

    def compute(self) -> None:
        mg = bench.mass_to_weigh_mg(parse_number(self.conc.text(), "the concentration"), self.unit.currentText(),
                                    parse_number(self.vol.text(), "the volume"), parse_number(self.mw.text(), "the molar mass"))
        self.answer(f"Weigh <b>{g(mg)} mg</b> ({g(mg / 1000)} g) and make up to {self.vol.text().strip()} mL.")


PAGES = (DilutionPage, SerialPage, AdditionPage, SignalPage, NernstPage, IonicStrengthPage, WeighPage)


class BenchDialog(QDialog):
    """All the calculators in one window.  Built once and shown again, so what was typed is still there."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Bench calculators")
        self.setMinimumSize(640, 560)
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.pages: dict[str, _Calc] = {}
        for cls in PAGES:
            page = cls()
            self.pages[cls.TITLE] = page
            self.tabs.addTab(page, cls.TITLE)
        lay.addWidget(self.tabs)

    def show_page(self, title: str) -> None:
        self.tabs.setCurrentWidget(self.pages[title])
