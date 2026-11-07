"""Settings: appearance, plot style, analysis defaults, report output and files. Every change applies at once."""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
    QPushButton, QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import plots, theme
from ..prefs import prefs
from .mpl_canvas import PlotWidget
from .ui_kit import Card

PAGES = ("Appearance", "Plots", "Analysis", "Reports and export", "Files and storage")


def _hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("SettingsHint")
    lab.setWordWrap(True)
    return lab


def _title(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("SettingsTitle")
    return lab


def _combo(items: list[tuple[str, object]]) -> QComboBox:
    c = QComboBox()
    for text, data in items:
        c.addItem(text, data)
    return c


class SettingsDialog(QDialog):
    def __init__(self, set_theme: Callable[[str], None], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(900, 620)
        self._set_theme = set_theme
        self._loading = False
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)
        self.nav = QListWidget()
        self.nav.setObjectName("NavList")
        self.nav.setFixedWidth(200)
        self.nav.addItems(PAGES)
        for i in range(self.nav.count()):
            self.nav.item(i).setSizeHint(QSize(0, 38))                  # the style sheet's padding does not size rows
        root.addWidget(self.nav)
        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)
        for build in (self._appearance, self._plots, self._analysis, self._reports, self._files):
            self.pages.addWidget(build())
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        theme.bus.changed.connect(self._redraw_preview)
        self._load()

    # ------------------------------------------------------------------ pages
    def _page(self, title: str, hint: str) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(12)
        lay.addWidget(_title(title))
        lay.addWidget(_hint(hint))
        return w, lay

    def _appearance(self) -> QWidget:
        w, lay = self._page("Appearance", "How the window looks. Reports are always drawn on white, for print.")
        self.theme_box = _combo([("Match the system", "system"), ("Light", "light"), ("Dark", "dark")])
        self.theme_box.currentIndexChanged.connect(lambda _i: None if self._loading else self._set_theme(self.theme_box.currentData()))
        card = Card("Theme")
        f = QFormLayout()
        f.addRow("Colour theme", self.theme_box)
        card.body.addLayout(f)
        lay.addWidget(card)
        lay.addStretch(1)
        return w

    def _plots(self) -> QWidget:
        w, lay = self._page("Plots", "Applies to every plot in the window and in exported reports. "
                                     "Publication style gives boxed axes, inward ticks and serif text, as in a journal figure.")
        self.style_box = _combo([("Modern (grid, open axes)", "modern"), ("Publication (boxed, inward ticks, serif)", "publication")])
        self.palette_box = _combo([("Default", "default"), ("Colour-blind safe (Okabe–Ito)", "colorblind"), ("Greyscale (black-and-white print)", "greyscale")])
        self.font = QSlider(Qt.Horizontal)
        self.font.setRange(80, 160)
        self.font.setSingleStep(5)
        self.font.setPageStep(10)
        self.font_label = QLabel("100 %")
        for box, key in ((self.style_box, "plot_style"), (self.palette_box, "plot_palette")):
            box.currentIndexChanged.connect(lambda _i, k=key, b=box: self._plot_pref(k, b.currentData()))
        self.font.valueChanged.connect(lambda v: self._plot_pref("plot_font_scale", v / 100.0))
        card = Card("Style")
        f = QFormLayout()
        f.addRow("Plot style", self.style_box)
        f.addRow("Colours", self.palette_box)
        row = QHBoxLayout()
        row.addWidget(self.font, 1)
        row.addWidget(self.font_label)
        f.addRow("Text size", row)
        card.body.addLayout(f)
        lay.addWidget(card)
        prev = Card("Preview", margins=8)
        self.preview = PlotWidget(figsize=(6, 3.4), toolbar=False)
        self.preview.setMinimumHeight(250)
        prev.body.addWidget(self.preview, 1)
        lay.addWidget(prev, 1)
        lay.addWidget(_hint("Text size changes axis labels, tick labels, titles and legends."))
        return w

    def _analysis(self) -> QWidget:
        w, lay = self._page("Analysis defaults", "Starting values for new work. A setting you have already changed in a project is kept.")
        self.lod_k = QDoubleSpinBox()
        self.lod_k.setRange(1.0, 10.0)
        self.lod_k.setDecimals(1)
        self.lod_k.setSingleStep(0.1)
        self.lod_k.setToolTip("LOD = k·σ/S. 3 is the common IUPAC-style choice, 3.3 follows ICH; LOQ keeps the 10σ/S convention.")
        self.lod_k.setKeyboardTracking(False)
        self.lod_k.valueChanged.connect(lambda v: None if self._loading else prefs.set("lod_k", v))
        self.conc_box = _combo([(u, u) for u in ("M", "mM", "µM", "nM", "pM", "fM")])
        self.conc_box.currentIndexChanged.connect(lambda _i: None if self._loading else prefs.set("conc_unit", self.conc_box.currentData()))
        card = Card("Calibration and the sensor study")
        f = QFormLayout()
        f.addRow("LOD factor k", self.lod_k)
        f.addRow("Default concentration unit", self.conc_box)
        card.body.addLayout(f)
        lay.addWidget(card)
        lay.addWidget(_hint("k = 3 → LOD = 3σ/S, LOQ = 10σ/S.  k = 3.3 → LOD = 3.3σ/S, LOQ = 11σ/S.  The unit is used for new rows in the Responses table."))
        lay.addStretch(1)
        return w

    def _reports(self) -> QWidget:
        w, lay = self._page("Reports and export", "What goes on the cover of a PDF report and how figures are written.")
        self.operator = QLineEdit()
        self.operator.setPlaceholderText("Your name")
        self.institution = QLineEdit()
        self.institution.setPlaceholderText("Lab or institution")
        self.operator.editingFinished.connect(lambda: None if self._loading else prefs.set("operator", self.operator.text()))
        self.institution.editingFinished.connect(lambda: None if self._loading else prefs.set("institution", self.institution.text()))
        self.dpi = _combo([("150 dpi (small files)", 150), ("200 dpi (default)", 200), ("300 dpi (print)", 300), ("600 dpi (journal line art)", 600)])
        self.page = _combo([("A4", "A4"), ("US Letter", "Letter")])
        self.dpi.currentIndexChanged.connect(lambda _i: None if self._loading else prefs.set("export_dpi", self.dpi.currentData()))
        self.page.currentIndexChanged.connect(lambda _i: None if self._loading else prefs.set("page_size", self.page.currentData()))
        card = Card("Report")
        f = QFormLayout()
        f.addRow("Analyst", self.operator)
        f.addRow("Institution", self.institution)
        f.addRow("PDF page size", self.page)
        f.addRow("Figure resolution", self.dpi)
        card.body.addLayout(f)
        lay.addWidget(card)
        lay.addWidget(_hint("The analyst name is also filled into new projects. Higher resolution makes larger PDF and Excel files."))
        lay.addStretch(1)
        return w

    def _files(self) -> QWidget:
        w, lay = self._page("Files and storage", "Settings are kept in a small file in your user folder; projects hold their own data.")
        self.recent = QSpinBox()
        self.recent.setRange(1, 12)
        self.recent.valueChanged.connect(lambda v: None if self._loading else prefs.set("recent_limit", v))
        self.where = QLabel("")
        self.where.setObjectName("SettingsHint")
        self.where.setWordWrap(True)
        self.where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.support_popup = QCheckBox("Show the “buy me a coffee” card when the app starts")
        self.support_popup.toggled.connect(lambda on: None if self._loading else prefs.set("support_popup", bool(on)))
        card = Card("Start page")
        f = QFormLayout()
        f.addRow("Recent projects shown", self.recent)
        card.body.addLayout(f)
        card.body.addWidget(self.support_popup)
        lay.addWidget(card)
        card = Card("Storage")
        card.body.addWidget(self.where)
        row = QHBoxLayout()
        show = QPushButton("Show settings folder")
        show.clicked.connect(self._show_folder)
        reset = QPushButton("Reset all settings…")
        reset.clicked.connect(self._reset)
        row.addWidget(show)
        row.addWidget(reset)
        row.addStretch(1)
        card.body.addLayout(row)
        lay.addWidget(card)
        lay.addWidget(_hint("There is no autosave: save your project to keep your work. Closing or opening another project always asks first."))
        lay.addStretch(1)
        return w

    # ------------------------------------------------------------------ behaviour
    def _load(self) -> None:
        """Show the current preferences without triggering any change."""
        self._loading = True
        try:
            blockers = [QSignalBlocker(wd) for wd in (self.theme_box, self.style_box, self.palette_box, self.font, self.lod_k,
                                                      self.conc_box, self.dpi, self.page, self.recent, self.support_popup)]
            self.theme_box.setCurrentIndex(max(self.theme_box.findData(theme.preference()), 0))
            self.style_box.setCurrentIndex(max(self.style_box.findData(prefs.get("plot_style")), 0))
            self.palette_box.setCurrentIndex(max(self.palette_box.findData(prefs.get("plot_palette")), 0))
            self.font.setValue(int(round(prefs.get("plot_font_scale") * 100)))
            self.font_label.setText(f"{self.font.value()} %")
            self.lod_k.setValue(float(prefs.get("lod_k")))
            self.conc_box.setCurrentIndex(max(self.conc_box.findData(prefs.get("conc_unit")), 0))
            self.dpi.setCurrentIndex(max(self.dpi.findData(prefs.get("export_dpi")), 0))
            self.page.setCurrentIndex(max(self.page.findData(prefs.get("page_size")), 0))
            self.recent.setValue(int(prefs.get("recent_limit")))
            self.support_popup.setChecked(bool(prefs.get("support_popup")))
            del blockers
            self.operator.setText(prefs.get("operator"))
            self.institution.setText(prefs.get("institution"))
            self.where.setText(f"Settings file: {prefs.path}")
        finally:
            self._loading = False
        self._redraw_preview()

    def _plot_pref(self, key: str, value) -> None:
        if self._loading:
            return
        prefs.set(key, value)
        self.font_label.setText(f"{round(prefs.get('plot_font_scale') * 100)} %")
        theme.refresh_plots()                      # every plot in the app redraws, this dialog's preview included

    def _redraw_preview(self) -> None:
        plots.style_preview(self.preview.fig, theme.current())
        self.preview.redraw()

    def _show_folder(self) -> None:
        folder = prefs.path.parent
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _reset(self) -> None:
        if QMessageBox.question(self, "Reset settings", "Reset every setting to its default?") != QMessageBox.Yes:
            return
        prefs.reset()
        self._set_theme("system")
        theme.refresh_plots()
        self._load()
