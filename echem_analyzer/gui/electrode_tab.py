"""Electrode characterisation from CV series: scan-rate (Randles–Sevcik, ECSA, Laviron) and double-layer capacitance."""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .. import electrode, electrode_study as estudy, plots, theme
from ..analyzers import g
from ..cv_analysis import CVRun
from ..models import CVSettings
from .mpl_canvas import PlotWidget
from .session import Session, file_short, run_label
from .ui_kit import Card, KpiCard, Notice, Splitter, hint, persist_splitters

C_USE, C_RUN, C_NU, C_CYC = range(4)


class RunTable(QWidget):
    """CV runs with a Use box, an editable scan rate and the cycle to use; extra read-only columns follow."""

    def __init__(self, session: Session, key: str, extra_cols: list[str], on_change):
        super().__init__()
        self.session, self.key, self.on_change = session, key, on_change
        self._runs: list[CVRun] = []
        self._updating = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 4 + len(extra_cols))
        self.table.setHorizontalHeaderLabels(["Use", "Run", "Scan rate (V/s)", "Cycle"] + extra_cols)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._edited)
        lay.addWidget(self.table)

    def state(self) -> dict:
        return self.session.extras.setdefault("electrode", {}).setdefault(self.key, {"use": {}, "cycle": {}})

    def rebuild(self, extras: Optional[dict[str, list[str]]] = None) -> None:
        self._runs = self.session.cv_runs()
        st = self.state()
        self._updating = True
        try:
            self.table.setRowCount(len(self._runs))
            for i, run in enumerate(self._runs):
                s = self.session.cv_settings_for(run)             # reading must not mark the run as analysed
                it = QTableWidgetItem("")
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if st["use"].get(run.uid, False) else Qt.Unchecked)
                self.table.setItem(i, C_USE, it)
                name = QTableWidgetItem(run_label(run))
                name.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.table.setItem(i, C_RUN, name)
                nu = QTableWidgetItem(f"{s.scan_rate:g}" if s.scan_rate > 0 else "")
                nu.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                nu.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, C_NU, nu)
                cb = QComboBox()
                cb.addItem("last used", 0)
                for n in run.cycle_numbers:
                    cb.addItem(f"cycle {n}", n)
                cb.setCurrentIndex(max(cb.findData(st["cycle"].get(run.uid, 0)), 0))
                cb.currentIndexChanged.connect(lambda _i, uid=run.uid, box=cb: self._cycle(uid, box.currentData()))
                self.table.setCellWidget(i, C_CYC, cb)
                for j, text in enumerate((extras or {}).get(run.uid, []), start=4):
                    t = QTableWidgetItem(text)
                    t.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    t.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.table.setItem(i, j, t)
            self.table.resizeColumnsToContents()
            self.table.setColumnWidth(C_RUN, min(self.table.columnWidth(C_RUN), 330))
        finally:
            self._updating = False
        self.table.doItemsLayout()                  # place the new cell widgets now, not on the next event-loop turn

    def _cycle(self, uid: str, n: int) -> None:
        self.state()["cycle"][uid] = n
        self.session.touch()
        self.on_change()

    def _edited(self, it: QTableWidgetItem) -> None:
        if self._updating or it.row() >= len(self._runs):
            return
        run = self._runs[it.row()]
        if it.column() == C_USE:
            self.state()["use"][run.uid] = it.checkState() == Qt.Checked
        elif it.column() == C_NU:
            try:
                self.session.get_cv_settings(run).scan_rate = max(float(it.text().replace(",", ".") or 0), 0.0)
            except ValueError:
                pass
        self.session.touch()
        self.on_change()

    def selected(self) -> list[tuple[CVRun, CVSettings, int]]:
        """(run, its settings, the cycle number to use) for every ticked run."""
        return estudy.select_runs(self.session.cv_runs(), self.session.cv_settings_for, self.state())


class ElectrodeTab(QWidget):
    def __init__(self, session: Session, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.session = session
        self.analysis: Optional[electrode.ScanRateAnalysis] = None
        self.cdl_out: dict = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("SubTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_scan_rate(), "Scan-rate study")
        self.tabs.addTab(self._build_cdl(), "Double layer (Cdl)")
        root.addWidget(self.tabs)
        session.files_changed.connect(self.rebuild)
        theme.bus.changed.connect(self.recompute)
        self.rebuild()

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _spin(lo, hi, dec, step, val, suffix="", tip="") -> QDoubleSpinBox:
        w = QDoubleSpinBox()
        w.setRange(lo, hi)
        w.setDecimals(dec)
        w.setSingleStep(step)
        w.setValue(val)
        w.setKeyboardTracking(False)
        if suffix:
            w.setSuffix(f" {suffix}")
        if tip:
            w.setToolTip(tip)
        return w

    def _side(self, kpi_labels: list[str]):
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(2, 2, 10, 2)
        lay.setSpacing(10)
        kpis = [KpiCard(t) for t in kpi_labels]
        grid = QGridLayout()
        grid.setSpacing(8)
        for k, w in enumerate(kpis):
            grid.addWidget(w, k // 2, k % 2)
        lay.addLayout(grid)
        notice = Notice()
        lay.addWidget(notice)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(280)                       # the user can drag the grip to make it wider or narrower
        scroll.setFrameShape(QFrame.NoFrame)
        return scroll, lay, kpis, notice

    # ------------------------------------------------------------------ scan-rate study
    def _build_scan_rate(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        split = Splitter(Qt.Horizontal)
        lay.addWidget(split)
        left = Splitter(Qt.Vertical)
        pc = Card(margins=8)
        self.sr_plot = PlotWidget(figsize=(7.5, 5))
        pc.body.addWidget(self.sr_plot, 1)
        left.addWidget(pc)
        tc = Card("CV runs at different scan rates", margins=10)
        self.sr_runs = RunTable(self.session, "scan_rate", ["ip a (µA)", "ip c (µA)", "Ep a (V)", "Ep c (V)"], self.recompute)
        tc.body.addWidget(self.sr_runs, 1)
        left.addWidget(tc)
        left.setSizes([520, 220])
        split.addWidget(left)
        scroll, side, self.sr_kpis, self.sr_notice = self._side(["ECSA", "Roughness", "log ip – log ν", "α · ks (Laviron)"])
        card = Card("Redox probe and electrode")
        f = QFormLayout()
        self.n_e = QSpinBox()
        self.n_e.setRange(1, 6)
        self.n_e.setValue(1)
        self.conc = self._spin(0, 1e4, 3, 0.5, 5.0, "mM", "e.g. 5 mM [Fe(CN)6]3−/4− in 0.1 M KCl")
        self.diff = self._spin(0, 1.0, 9, 1e-6, 7.6e-6, "cm²/s", "[Fe(CN)6]3− in 0.1 M KCl: about 7.6e-6 cm²/s")
        self.diff.setDecimals(9)
        self.area = self._spin(0, 1e3, 5, 0.01, 0.0, "cm²", "Geometric area, for the roughness factor.")
        self.temp = self._spin(-10, 90, 1, 1, 25.0, "°C")
        for label, wd in (("Electrons (n)", self.n_e), ("Concentration", self.conc), ("Diffusion coeff. D", self.diff),
                          ("Geometric area", self.area), ("Temperature", self.temp)):
            f.addRow(label, wd)
        card.body.addLayout(f)
        side.addWidget(card)
        card = Card("Send to the study")
        self.sr_group = QLineEdit()
        self.sr_group.setPlaceholderText("Electrode name, e.g. E1")
        card.body.addWidget(self.sr_group)
        b = QPushButton("Add ECSA, roughness and exponent to study")
        b.clicked.connect(self.send_scan_rate)
        card.body.addWidget(b)
        side.addWidget(card)
        side.addStretch(1)
        split.addWidget(scroll)
        split.setStretchFactor(0, 1)
        split.setSizes([1000, 372])
        for wd in (self.n_e, self.conc, self.diff, self.area, self.temp):
            wd.valueChanged.connect(self._params_changed)
        return w

    # ------------------------------------------------------------------ Cdl
    def _build_cdl(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        split = Splitter(Qt.Horizontal)
        lay.addWidget(split)
        left = Splitter(Qt.Vertical)
        pc = Card(margins=8)
        self.cdl_plot = PlotWidget(figsize=(6, 4))
        pc.body.addWidget(self.cdl_plot, 1)
        left.addWidget(pc)
        tc = Card("Non-faradaic CV runs at different scan rates", margins=10)
        self.cdl_runs = RunTable(self.session, "cdl", ["(ia − ic)/2 (µA)"], self.recompute)
        tc.body.addWidget(self.cdl_runs, 1)
        left.addWidget(tc)
        left.setSizes([480, 240])
        split.addWidget(left)
        scroll, side, self.cdl_kpis, self.cdl_notice = self._side(["Cdl", "Cdl per area", "Roughness", "R²"])
        card = Card("Settings")
        f = QFormLayout()
        self.e_mid = self._spin(-5, 5, 3, 0.01, 0.0, "V", "A potential in the non-faradaic window, where the current is only charging.")
        self.cdl_area = self._spin(0, 1e3, 5, 0.01, 0.0, "cm²")
        self.cs = self._spin(0, 1000, 1, 5, 0.0, "µF/cm²", "Specific capacitance of a smooth surface of the same material (e.g. ~20–60 for metals); gives the roughness factor.")
        for label, wd in (("Mid potential E_mid", self.e_mid), ("Geometric area", self.cdl_area), ("Specific capacitance", self.cs)):
            f.addRow(label, wd)
            wd.valueChanged.connect(self._params_changed)
        card.body.addLayout(f)
        card.body.addWidget(hint("Record CVs in a potential window with no faradaic reaction, at several scan rates. The gap between the "
                                 "anodic and cathodic current at E_mid is 2·Cdl·ν."))
        side.addWidget(card)
        side.addStretch(1)
        split.addWidget(scroll)
        split.setStretchFactor(0, 1)
        split.setSizes([1000, 372])
        return w

    # ------------------------------------------------------------------ data
    def rebuild(self) -> None:
        self.recompute()

    def _params_changed(self, *_a) -> None:
        self.session.extras.setdefault("electrode", {})["params"] = {
            "n": self.n_e.value(), "conc": self.conc.value(), "D": self.diff.value(), "area": self.area.value(), "temp": self.temp.value(),
            "e_mid": self.e_mid.value(), "cdl_area": self.cdl_area.value(), "cs": self.cs.value()}
        self.session.touch()
        self.recompute()

    def _load_params(self) -> None:
        p = self.session.extras.get("electrode", {}).get("params")
        if not p:
            return
        for wd, key in ((self.n_e, "n"), (self.conc, "conc"), (self.diff, "D"), (self.area, "area"), (self.temp, "temp"),
                        (self.e_mid, "e_mid"), (self.cdl_area, "cdl_area"), (self.cs, "cs")):
            if key in p:
                with QSignalBlocker(wd):
                    wd.setValue(p[key])

    def params(self) -> estudy.ElectrodeParams:
        return estudy.ElectrodeParams(n=self.n_e.value(), conc=self.conc.value(), D=self.diff.value(), area=self.area.value(),
                                      temp=self.temp.value(), e_mid=self.e_mid.value(), cdl_area=self.cdl_area.value(), cs=self.cs.value())

    def recompute(self) -> None:
        self._load_params()
        p = theme.current()
        # --- scan-rate study
        prm = self.params()
        self.analysis, per_run = estudy.run_scan_rate(self.sr_runs.selected(), prm)
        self.sr_runs.rebuild({uid: [g(v, 4) for v in vals] for uid, vals in per_run.items()})
        plots.electrode_scan_rate(self.sr_plot.fig, self.analysis, p)
        self.sr_plot.redraw()
        a = self.analysis
        if a is None:
            for k in self.sr_kpis:
                k.set("—")
            self.sr_notice.show_message("Tick at least 3 runs and give each its scan rate (a .pssession file provides it automatically).", "neutral")
        else:
            ec = [v for v in (a.ecsa_a, a.ecsa_c) if math.isfinite(v)]
            exp = np.nanmean([a.loglog_a, a.loglog_c])
            lv = a.laviron
            self.sr_kpis[0].set(g(float(np.mean(ec)), 4) if ec else "—", "cm² (from the ip–√ν slope)" if ec else "needs concentration and D")
            self.sr_kpis[1].set(g(a.roughness, 3), "ECSA / geometric area" if math.isfinite(a.roughness) else "needs the geometric area")
            self.sr_kpis[2].set(g(float(exp), 3), a.regime, "success" if "diffusion" in a.regime else "warning")
            self.sr_kpis[3].set(f"{lv.alpha:.2f} · {lv.ks:.3g}" if lv and math.isfinite(lv.alpha) else "—",
                                "α · ks (s⁻¹), surface-confined" if lv and math.isfinite(lv.alpha) else "needs ΔEp > 200/n mV")
            if a.warnings:
                self.sr_notice.show_message("<br>".join("⚠ " + w for w in a.warnings), "warning")
            else:
                self.sr_notice.setVisible(False)
        # --- Cdl
        nu, di, self.cdl_out, half = estudy.run_cdl(self.cdl_runs.selected(), prm)
        self.cdl_runs.rebuild({uid: [g(v, 4)] for uid, v in half.items()})
        plots.electrode_cdl(self.cdl_plot.fig, nu, di, self.cdl_out, p)
        self.cdl_plot.redraw()
        o = self.cdl_out
        self.cdl_kpis[0].set(g(o.get("cdl_uF"), 4) if o else "—", f"µF ± {g(o.get('cdl_se'), 2)}" if o else "")
        self.cdl_kpis[1].set(g(o.get("per_area"), 4) if o else "—", "µF/cm²")
        self.cdl_kpis[2].set(g(o.get("roughness"), 3) if o else "—", "Cdl / specific capacitance")
        self.cdl_kpis[3].set(g(o.get("r2"), 4) if o else "—", "linearity", "success" if o and o.get("r2", 0) >= 0.995 else "")
        if o:
            self.cdl_notice.setVisible(False)
        else:
            self.cdl_notice.show_message("Tick at least 3 runs recorded at different scan rates.", "neutral")

    # ------------------------------------------------------------------ study
    def send_scan_rate(self) -> int:
        a = self.analysis
        if a is None:
            return 0
        group = self.sr_group.text().strip()
        name = f"scan-rate study · {group}" if group else "scan-rate study"
        items = []
        ec = [v for v in (a.ecsa_a, a.ecsa_c) if math.isfinite(v)]
        if ec:
            items.append(("ECSA", "cm²", float(np.mean(ec))))
        if math.isfinite(a.roughness):
            items.append(("Roughness factor", "", a.roughness))
        ex = float(np.nanmean([a.loglog_a, a.loglog_c]))
        if math.isfinite(ex):
            items.append(("log ip – log ν slope", "", ex))
        for q, u, v in items:
            self.session.upsert_named(f"electrode|{group}|{q}", q, u, v, name, "CV", "electrode", group)
        return len(items)

    def get_state(self) -> dict:
        return {"sub": self.tabs.currentIndex()}

    def set_state(self, state: dict) -> None:
        self.recompute()
        sub = (state or {}).get("sub")
        if isinstance(sub, int) and 0 <= sub < self.tabs.count():
            self.tabs.setCurrentIndex(sub)
