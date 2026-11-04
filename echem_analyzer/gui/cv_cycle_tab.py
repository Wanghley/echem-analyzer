"""Multi-cycle CV workspace: peaks per cycle, charge, and how far the cycles still change."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import plots, theme
from ..cv_analysis import CVRun, analyze_run, summarize_stability
from ..models import CVCycleResult, CVSettings
from ..reporting import cv_cycle_table, cv_summary_rows, g, write_csv
from .mpl_canvas import PlotWidget
from .preset_bar import PresetBar
from .session import Session, file_short, run_label
from .ui_kit import Card, KpiCard, Splitter, hint, persist_splitters

STARTUP_FACTOR = 8.0          # a first sample this many times the typical |i| is a start-up transient
HEADERS = ["Use", "Cycle", "E pa (V)", "i pa (µA)", "E pc (V)", "i pc (µA)", "E½ (V)", "ΔEp (V)",
           "|ipa/ipc|", "Q a (µC)", "Q c (µC)", "C app (µF)", "Δ i pa (%)"]
COL_USE, COL_CYCLE = 0, 1


class CVCycleTab(QWidget):
    def __init__(self, session: Session, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.session = session
        self.run: Optional[CVRun] = None
        self.s: Optional[CVSettings] = None
        self.results: list[CVCycleResult] = []
        self._loading = False
        self._tbl_updating = False
        self._build_ui()
        session.files_changed.connect(self.refresh_runs)
        theme.bus.changed.connect(self._on_theme)
        self.refresh_runs()

    # ================================================================== UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(10)
        lab = QLabel("CV RUN")
        lab.setObjectName("CardTitle")
        top.addWidget(lab)
        self.run_combo = QComboBox()
        self.run_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.run_combo.setMinimumContentsLength(30)
        self.run_combo.currentIndexChanged.connect(self._on_run_selected)
        top.addWidget(self.run_combo, 1)
        self.chk_branches = QCheckBox("Sweep direction")
        self.chk_branches.setToolTip("Draw the negative-going (cathodic) sweep dashed.")
        self.chk_peaks = QCheckBox("Mark peaks")
        self.chk_peaks.setChecked(True)
        self.chk_excluded = QCheckBox("Show excluded")
        self.chk_excluded.setChecked(True)
        self.chk_zoom = QCheckBox("Zoom to features")
        self.chk_zoom.setChecked(True)
        self.chk_zoom.setToolTip("Ignore the first 5 % of each cycle when scaling the y-axis: start-up charging "
                                 "currents (often −100 µA or more in cycle 1) would otherwise flatten the curves.")
        for c in (self.chk_branches, self.chk_peaks, self.chk_excluded, self.chk_zoom):
            c.toggled.connect(lambda _v: self.recompute(user_edit=False))
            top.addWidget(c)
        self.btn_export = QPushButton("Export cycle table…")
        self.btn_export.clicked.connect(self.export_clicked)
        top.addWidget(self.btn_export)
        root.addLayout(top)

        split = Splitter(Qt.Horizontal)
        root.addWidget(split, 1)

        left = Splitter(Qt.Vertical)
        plot_row = Splitter(Qt.Horizontal)
        cv_card = Card(margins=8)
        self.cv_plot = PlotWidget(figsize=(6, 4))
        self.ax_cv = self.cv_plot.fig.add_subplot(111)
        cv_card.body.addWidget(self.cv_plot, 1)
        tr_card = Card(margins=8)
        self.trend_plot = PlotWidget(figsize=(4.2, 4))
        tr_card.body.addWidget(self.trend_plot, 1)
        plot_row.addWidget(cv_card)
        plot_row.addWidget(tr_card)
        plot_row.setStretchFactor(0, 3)
        plot_row.setStretchFactor(1, 2)
        plot_row.setSizes([700, 480])
        left.addWidget(plot_row)

        tbl_card = Card(margins=10)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._on_table_edit)
        self.table.setToolTip(
            "One row per cycle. Untick 'Use' to leave a cycle out of the plots, trends and stability figures.\n"
            "i pa / i pc are baseline-corrected heights when a baseline is selected.\n"
            "C app = (Q a − Q c) / (2·ΔE): an apparent capacitance that also contains any Faradaic current.\n"
            "Δ i pa = change of the anodic peak relative to the previous used cycle."
        )
        tbl_card.body.addWidget(self.table, 1)
        left.addWidget(tbl_card)
        left.setStretchFactor(0, 4)
        left.setStretchFactor(1, 2)
        left.setSizes([600, 215])
        self.table.setMinimumHeight(150)
        split.addWidget(left)
        split.addWidget(self._build_side_panel())
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([1000, 372])

    def _spin(self, lo, hi, dec, step, suffix="") -> QDoubleSpinBox:
        sb = QDoubleSpinBox()
        sb.setRange(lo, hi)
        sb.setDecimals(dec)
        sb.setSingleStep(step)
        sb.setKeyboardTracking(False)
        if suffix:
            sb.setSuffix(f" {suffix}")
        sb.valueChanged.connect(lambda _v: self._on_controls_changed())
        return sb

    def _window_row(self, label: str) -> tuple[QCheckBox, QDoubleSpinBox, QDoubleSpinBox, QWidget]:
        chk = QCheckBox(label)
        lo, hi = self._spin(-5, 5, 3, 0.01, "V"), self._spin(-5, 5, 3, 0.01, "V")
        chk.toggled.connect(lambda _v: self._on_controls_changed())
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(lo)
        row.addWidget(QLabel("to"))
        row.addWidget(hi)
        w = QWidget()
        w.setLayout(row)
        return chk, lo, hi, w

    def _build_side_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(2, 2, 10, 2)
        lay.setSpacing(10)

        self.kpi_cycles, self.kpi_ipa = KpiCard("Cycles used"), KpiCard("Anodic ip (last)")
        self.kpi_change, self.kpi_status = KpiCard("Last change"), KpiCard("Conditioning")
        grid = QGridLayout()
        grid.setSpacing(8)
        for k, w in enumerate((self.kpi_cycles, self.kpi_ipa, self.kpi_change, self.kpi_status)):
            grid.addWidget(w, k // 2, k % 2)
        lay.addLayout(grid)

        card = Card("Summary")
        self.summary_label = QLabel()
        self.summary_label.setTextFormat(Qt.RichText)
        self.summary_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.summary_label.setWordWrap(True)
        card.body.addWidget(self.summary_label)
        lay.addWidget(card)

        card = Card("Setup presets")
        self.preset_bar = PresetBar(
            self.session, "cv", get_current=lambda: self.s,
            get_all=lambda: [self.session.get_cv_settings(r) for r in self.session.cv_runs()],
        )
        self.preset_bar.applied.connect(self._on_preset_applied)
        card.body.addWidget(self.preset_bar)
        lay.addWidget(card)

        card = Card("Cycles")
        grid = QGridLayout()
        grid.setSpacing(6)
        for k, (text, tip, fn) in enumerate((
            ("All", "Use every cycle", lambda ns: []),
            ("Skip first", "Leave cycle 1 out (it often carries the start-up transient)", lambda ns: ns[:1]),
            ("First + last", "Keep only the first and the last cycle", lambda ns: ns[1:-1]),
            ("Last only", "Keep only the last cycle", lambda ns: ns[:-1]),
        )):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _c=False, f=fn: self._set_excluded(f))
            grid.addWidget(b, k // 2, k % 2)
        card.body.addLayout(grid)
        card.body.addWidget(hint("Tick or untick individual cycles in the table below the plots."))
        lay.addWidget(card)

        card = Card("Peak search")
        f = QFormLayout()
        self.chk_an, self.an_lo, self.an_hi, w = self._window_row("Restrict anodic")
        f.addRow(self.chk_an)
        f.addRow(w)
        self.chk_ca, self.ca_lo, self.ca_hi, w = self._window_row("Restrict cathodic")
        f.addRow(self.chk_ca)
        f.addRow(w)
        self.base_combo = QComboBox()
        self.base_combo.addItem("None (raw current)", "none")
        self.base_combo.addItem("Linear across the window", "linear")
        self.base_combo.setToolTip("Linear: draw a straight line between the first and last point of the search "
                                   "window and measure the peak height from it.")
        self.base_combo.currentIndexChanged.connect(lambda _i: self._on_controls_changed())
        f.addRow("Baseline", self.base_combo)
        card.body.addLayout(f)
        card.body.addWidget(hint("Anodic peak = max on the positive-going sweep; cathodic = min on the negative-going sweep."))
        lay.addWidget(card)

        card = Card("Charge and stability")
        f = QFormLayout()
        self.rate = self._spin(0, 100, 4, 0.01, "V/s")
        self.rate.setToolTip("Needed for charge and capacitance. Read from .pssession files; CSV exports do not "
                             "contain it, so enter it (0 = unknown).")
        f.addRow("Scan rate", self.rate)
        self.tol = self._spin(0.01, 100, 2, 0.5, "%")
        self.tol.setToolTip("Peaks are 'stable' once they change by less than this between consecutive cycles.")
        f.addRow("Stable if change <", self.tol)
        card.body.addLayout(f)
        self.rate_note = hint("")
        card.body.addWidget(self.rate_note)
        lay.addWidget(card)
        lay.addStretch(1)

        self.panel = panel
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(280)                       # the user can drag the grip to make it wider or narrower
        scroll.setFrameShape(QFrame.NoFrame)
        return scroll

    # ================================================================== runs
    def refresh_runs(self) -> None:
        cur = self.run.uid if self.run else None
        runs = self.session.cv_runs()
        with QSignalBlocker(self.run_combo):
            self.run_combo.clear()
            for r in runs:
                self.run_combo.addItem(run_label(r), r.uid)
            i = self.run_combo.findData(cur) if cur else -1
            self.run_combo.setCurrentIndex(i if i >= 0 else (0 if runs else -1))
        has = bool(runs)
        self.panel.setEnabled(has)
        self.btn_export.setEnabled(has)
        if not has:
            self.run = self.s = None
            self.results = []
            self._show_empty()
            return
        uid = self.run_combo.currentData()
        match = next(r for r in runs if r.uid == uid)
        self.load_run(match)

    def select_run(self, uid: str) -> None:
        i = self.run_combo.findData(uid)
        if i >= 0:
            self.run_combo.setCurrentIndex(i)

    def select_run_of_curve(self, uid: str) -> None:
        for r in self.session.cv_runs():
            if any(c.uid == uid for c in r.cycles.values()):
                self.select_run(r.uid)
                return

    def _on_run_selected(self, _i: int) -> None:
        uid = self.run_combo.currentData()
        r = next((r for r in self.session.cv_runs() if r.uid == uid), None)
        if r is not None:
            self.load_run(r)

    def load_run(self, run: CVRun) -> None:
        self.run = run
        self.s = self.session.get_cv_settings(run)
        self._populate_controls()
        self.cv_plot.reset_toolbar()
        self.recompute(user_edit=False)

    # ---- project state
    def get_state(self) -> dict:
        return {"uid": self.run.uid if self.run else None,
                "display": {"branches": self.chk_branches.isChecked(), "peaks": self.chk_peaks.isChecked(),
                            "excluded": self.chk_excluded.isChecked(), "zoom": self.chk_zoom.isChecked()}}

    def set_state(self, state: dict) -> None:
        state = state or {}
        disp = state.get("display") or {}
        for chk, key in ((self.chk_branches, "branches"), (self.chk_peaks, "peaks"),
                         (self.chk_excluded, "excluded"), (self.chk_zoom, "zoom")):
            if key in disp:
                with QSignalBlocker(chk):
                    chk.setChecked(bool(disp[key]))
        if state.get("uid"):
            self.select_run(state["uid"])
        self.recompute(user_edit=False)

    # ================================================================== controls <-> settings
    def _populate_controls(self) -> None:
        s, run = self.s, self.run
        e_all = np.concatenate([c.x for c in run.cycles.values()])
        e_lo, e_hi = float(e_all.min()), float(e_all.max())
        self._loading = True
        try:
            for chk, lo, hi, win in ((self.chk_an, self.an_lo, self.an_hi, s.anodic_window),
                                     (self.chk_ca, self.ca_lo, self.ca_hi, s.cathodic_window)):
                chk.setChecked(win is not None)
                lo.setValue(win[0] if win else e_lo)          # an unrestricted window starts as the full range
                hi.setValue(win[1] if win else e_hi)
                lo.setEnabled(win is not None)
                hi.setEnabled(win is not None)
            i = self.base_combo.findData(s.baseline)
            self.base_combo.setCurrentIndex(i if i >= 0 else 0)
            self.rate.setValue(s.scan_rate)
            self.tol.setValue(s.stability_tol)
            self.rate_note.setText(
                "Read from the .pssession method." if run.scan_rate > 0
                else "Not stored in CSV exports: enter the scan rate to get charge and capacitance.")
        finally:
            self._loading = False

    def _on_controls_changed(self) -> None:
        if self._loading or self.s is None:
            return
        s = self.s
        s.anodic_window = [self.an_lo.value(), self.an_hi.value()] if self.chk_an.isChecked() else None
        s.cathodic_window = [self.ca_lo.value(), self.ca_hi.value()] if self.chk_ca.isChecked() else None
        for chk, lo, hi in ((self.chk_an, self.an_lo, self.an_hi), (self.chk_ca, self.ca_lo, self.ca_hi)):
            lo.setEnabled(chk.isChecked())
            hi.setEnabled(chk.isChecked())
        s.baseline = self.base_combo.currentData()
        s.scan_rate = self.rate.value()
        s.stability_tol = self.tol.value()
        self.recompute()

    def _on_preset_applied(self, _scope: str) -> None:
        if self.s is not None:
            self._populate_controls()
            self.recompute(user_edit=True)

    def _set_excluded(self, fn) -> None:
        if self.run is None or self.s is None:
            return
        self.s.excluded_cycles = list(fn(self.run.cycle_numbers))
        self.recompute()

    def _on_table_edit(self, item: QTableWidgetItem) -> None:
        if self._tbl_updating or self.s is None or item.column() != COL_USE:
            return
        n = int(self.table.item(item.row(), COL_CYCLE).text())
        ex = set(self.s.excluded_cycles)
        (ex.discard if item.checkState() == Qt.Checked else ex.add)(n)
        self.s.excluded_cycles = sorted(ex)
        self.recompute()

    # ================================================================== compute + render
    def recompute(self, user_edit: bool = True) -> None:
        if self._loading or self.run is None or self.s is None:
            return
        if user_edit:
            self.session.touch()
        self.results = analyze_run(self.run, self.s)
        self._fill_table()
        self._draw_cv()
        self._draw_trends()
        self._fill_summary()

    def _show_empty(self) -> None:
        self.ax_cv.clear()
        self.ax_cv.text(0.5, 0.5, "No cyclic-voltammetry runs loaded.\nOpen a PalmSens file that contains CV scans.",
                        ha="center", va="center", transform=self.ax_cv.transAxes, color="#6b7280")
        self.ax_cv.set_axis_off()
        self.cv_plot.redraw()
        self.trend_plot.fig.clear()
        self.trend_plot.redraw()
        self.table.setRowCount(0)
        self.summary_label.setText("")

    def _fill_table(self) -> None:
        _, rows = cv_cycle_table(self.run, self.s)
        self._tbl_updating = True
        try:
            self.table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                used = bool(row[1])
                # columns: Use | cycle | the ten measured values | change of i_pa vs the previous cycle
                for c, txt in enumerate(["", str(row[0])] + [g(v, 4) for v in row[2:12]] + [g(row[12], 3)]):
                    it = QTableWidgetItem(txt)
                    flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
                    if c == COL_USE:
                        flags |= Qt.ItemIsUserCheckable
                        it.setCheckState(Qt.Checked if used else Qt.Unchecked)
                    it.setFlags(flags)
                    if c >= COL_CYCLE:
                        it.setTextAlignment(Qt.AlignCenter if c == COL_CYCLE else Qt.AlignRight | Qt.AlignVCenter)
                    if not used:
                        it.setForeground(QBrush(QColor("#9ca3af")))
                    self.table.setItem(r, c, it)
            self.table.resizeColumnsToContents()
        finally:
            self._tbl_updating = False

    def _draw_cv(self) -> None:
        run, s = self.run, self.s
        plots.cv_cycles(self.ax_cv, run, s, self.results, theme.current(),
                        branches=self.chk_branches.isChecked(), peaks=self.chk_peaks.isChecked(),
                        show_excluded=self.chk_excluded.isChecked(), zoom=self.chk_zoom.isChecked(),
                        title=f"{file_short(run.source)} — {run.title}")
        self.cv_plot.redraw()

    def _draw_trends(self) -> None:
        plots.cv_trends(self.trend_plot.fig, self.results, self.s, theme.current())
        self.trend_plot.redraw()

    def _on_theme(self) -> None:
        if self.run is not None and self.s is not None:
            self.recompute(user_edit=False)

    def _startup_cycles(self) -> list[int]:
        """Cycles whose first sample is far larger than the rest of the cycle (start-up transient)."""
        out = []
        for r in self.results:
            y = self.run.cycles[r.cycle].y
            typical = float(np.median(np.abs(y)))
            if typical > 0 and abs(y[0]) > STARTUP_FACTOR * typical:
                out.append(r.cycle)
        return out

    def _fill_summary(self) -> None:
        run, s, p = self.run, self.s, theme.current()
        muted = f"color:{p.muted}"
        e = np.concatenate([c.x for c in run.cycles.values()])
        parts = [f"<p style='{muted}'>{len(run.cycles)} cycles · E {e.min():.2f} to {e.max():.2f} V"
                 + (f" · {g(s.scan_rate, 3)} V/s" if s.scan_rate > 0 else "") + "</p>",
                 "<table cellspacing='3'>"]
        for name, val, unit in cv_summary_rows(run, s):
            parts.append(f"<tr><td style='{muted}'>{name}</td><td align='right'><b>{val}</b></td>"
                         f"<td style='{muted}'>&nbsp;{unit}</td></tr>")
        parts.append("</table>")

        # headline tiles
        res = self.results
        self.kpi_cycles.set(f"{len(res)} / {len(run.cycles)}", "after exclusions" if len(res) != len(run.cycles) else "all used")
        if res:
            last = res[-1]
            self.kpi_ipa.set(g(last.i_pa, 4), "µA, anodic peak")
        else:
            self.kpi_ipa.set("—")
        if len(res) >= 2:
            st = summarize_stability(res, s.stability_tol)
            moving = [n for n, k in (("anodic", "i_pa"), ("cathodic", "i_pc")) if st[k].stable_from is None]
            ch = st["i_pa"].last_change_pct
            self.kpi_change.set(f"{g(ch, 3)} %", f"anodic, tolerance {g(s.stability_tol, 3)} %",
                                "warning" if ch >= s.stability_tol else "success")
            self.kpi_status.set("Settling" if moving else "Stable",
                                "still moving: " + " & ".join(moving) if moving else "within tolerance",
                                "warning" if moving else "success")
            if moving:
                parts.append(f"<p style='color:{p.warning}'>⚠ The {' and '.join(moving)} peak is still changing by more "
                             f"than {g(s.stability_tol, 3)} % per cycle. The electrode may need more conditioning "
                             "cycles before its response can be relied on.</p>")
            else:
                parts.append(f"<p style='color:{p.success}'>✓ Peaks have settled within the tolerance.</p>")
        else:
            self.kpi_change.set("—", "needs 2+ cycles")
            self.kpi_status.set("—", "")
        if s.baseline == "linear":
            free = [n for n, w in (("anodic", s.anodic_window), ("cathodic", s.cathodic_window)) if not w]
            if free:
                parts.append(f"<p style='{muted}'>The linear baseline only applies to a restricted window: the "
                             f"{' and '.join(free)} peak is measured from the raw current. Tick "
                             "“Restrict” and set a window around the peak.</p>")
        startup = self._startup_cycles()
        if startup:
            parts.append(f"<p style='color:{p.warning}'>⚠ Cycle(s) {', '.join(map(str, startup))} begin with a large "
                         f"start-up current (> {STARTUP_FACTOR:g}× the typical value). Their charge and capacitance are not "
                         "representative: consider unticking them.</p>")
        self.summary_label.setText("".join(parts))

    # ================================================================== export
    def export_csv(self, path: Path) -> int:
        header, rows = cv_cycle_table(self.run, self.s)
        write_csv(path, header, rows)
        return len(rows)

    def export_clicked(self) -> None:
        if self.run is None:
            return
        default = f"{Path(self.run.source).stem}_{self.run.title}_cycles.csv".replace(" ", "_")
        path, _ = QFileDialog.getSaveFileName(self, "Export cycle table", default, "CSV (*.csv)")
        if path:
            n = self.export_csv(Path(path))
            QMessageBox.information(self, "Export cycle table", f"Wrote {n} cycles to\n{path}")
