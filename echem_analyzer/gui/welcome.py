"""The page shown before any data is loaded: clear first steps instead of an empty plot."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import theme
from ..prefs import prefs
from .ui_kit import Card


# (title, one line, guide topic): what the platform is for, each tile opens the matching part of the guide
TILES = [
    ("Calibration and LOD", "Sensitivity, linear range, LOD and LOQ from peaks, currents or impedance", "calibration"),
    ("Voltammetry", "CV cycles, DPV / SWV / LSV peaks, baselines and widths", "peaks"),
    ("Impedance", "Equivalent-circuit fits, Rct, Nyquist and Bode plots", "eis"),
    ("Potentiometry and OCP", "Steady state, drift, response time, Nernstian slope", "potentiometry"),
    ("Reproducibility and stability", "RSD between electrodes, retention over time, selectivity, recovery", "repro"),
    ("Electrode characterisation", "Electroactive area, roughness, double-layer capacitance, kinetics", "electrode"),
]


class _Tile(QPushButton):
    def __init__(self, title: str, sub: str):
        super().__init__()
        self.setObjectName("Tile")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(250, 74)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(3)
        for text, name in ((title, "TileTitle"), (sub, "TileSub")):
            lab = QLabel(text)
            lab.setObjectName(name)
            lab.setWordWrap(True)
            lab.setAttribute(Qt.WA_TransparentForMouseEvents)
            lay.addWidget(lab)
        lay.addStretch(1)


class WelcomePage(QWidget):
    open_project = Signal()
    add_files = Signal()
    add_folder = Signal()
    open_recent = Signal(str)
    return_to_project = Signal()
    close_project = Signal()
    open_help = Signal(str)
    open_settings = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.addStretch(2)
        col = QVBoxLayout()
        col.setSpacing(14)
        col.setAlignment(Qt.AlignHCenter)

        self.logo = QLabel()
        self.logo.setAlignment(Qt.AlignCenter)
        col.addWidget(self.logo)
        t = QLabel("Echem Analyzer")
        t.setObjectName("WelcomeTitle")
        t.setAlignment(Qt.AlignCenter)
        col.addWidget(t)
        sub = QLabel("Sensor electrochemistry: calibration, voltammetry, impedance and potentiometry, from PalmSens and other instruments")
        sub.setObjectName("WelcomeSub")
        sub.setAlignment(Qt.AlignCenter)
        col.addWidget(sub)

        self.current_card = Card("Current project")
        self.current_card.setMaximumWidth(560)
        self.current_card.setMinimumWidth(480)
        self.current_name = QLabel("")
        self.current_name.setObjectName("WelcomeSub")
        self.current_card.body.addWidget(self.current_name)
        cur_row = QHBoxLayout()
        self.btn_return = QPushButton("  Return to project")
        self.btn_return.setProperty("primary", True)
        self.btn_close = QPushButton("  Close project")
        for b in (self.btn_return, self.btn_close):
            b.setMinimumHeight(34)
            b.setCursor(Qt.PointingHandCursor)
            cur_row.addWidget(b)
        self.btn_return.clicked.connect(self.return_to_project)
        self.btn_close.clicked.connect(self.close_project)
        self.current_card.body.addLayout(cur_row)
        self.current_card.setVisible(False)
        col.addWidget(self.current_card, 0, Qt.AlignHCenter)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.setAlignment(Qt.AlignHCenter)
        self.btn_project = QPushButton("  Open project…")
        self.btn_project.setProperty("primary", True)
        self.btn_files = QPushButton("  Add data files…")
        self.btn_folder = QPushButton("  Add data folder…")
        for b in (self.btn_project, self.btn_files, self.btn_folder):
            b.setMinimumHeight(36)
            b.setMinimumWidth(160)
            b.setCursor(Qt.PointingHandCursor)
            row.addWidget(b)
        self.btn_project.clicked.connect(self.open_project)
        self.btn_files.clicked.connect(self.add_files)
        self.btn_folder.clicked.connect(self.add_folder)
        col.addLayout(row)

        self.recent_card = Card("Recent projects")
        self.recent_card.setMaximumWidth(560)
        self.recent_card.setMinimumWidth(480)
        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(2)
        self.recent_card.body.addLayout(self.recent_box)
        col.addWidget(self.recent_card, 0, Qt.AlignHCenter)

        drop = QLabel("Drop PalmSens  .csv  ·  .pssession  ·  .echemproj  files anywhere in this window")
        drop.setObjectName("DropZone")
        drop.setAlignment(Qt.AlignCenter)
        drop.setMaximumWidth(560)
        drop.setMinimumWidth(480)
        col.addWidget(drop, 0, Qt.AlignHCenter)

        what = QLabel("WHAT ARE YOU ANALYSING?")
        what.setObjectName("SettingsHint")
        what.setAlignment(Qt.AlignCenter)
        col.addSpacing(6)
        col.addWidget(what)
        grid = QGridLayout()
        grid.setSpacing(10)
        for k, (title, sub, topic) in enumerate(TILES):
            tile = _Tile(title, sub)
            tile.setToolTip("Open the guide for this")
            tile.clicked.connect(lambda _c=False, t=topic: self.open_help.emit(t))
            grid.addWidget(tile, k // 3, k % 3)
        grid_box = QWidget()
        grid_box.setLayout(grid)
        grid_box.setMaximumWidth(820)
        col.addWidget(grid_box, 0, Qt.AlignHCenter)
        links = QHBoxLayout()
        links.setAlignment(Qt.AlignHCenter)
        for text, fn in (("Guide", lambda: self.open_help.emit("start")), ("Keyboard shortcuts", lambda: self.open_help.emit("shortcuts")),
                         ("Settings", lambda: self.open_settings.emit())):
            b = QPushButton(text)
            b.setObjectName("LinkButton")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, f=fn: f())
            links.addWidget(b)
        col.addLayout(links)

        outer.addLayout(col)
        outer.addStretch(3)
        theme.bus.changed.connect(self.restyle)
        self.restyle()

    def restyle(self) -> None:
        self.logo.setPixmap(theme.app_logo(68))
        p = theme.current()
        self.btn_project.setIcon(theme.icon("folder-open", p.accent_text))
        self.btn_files.setIcon(theme.icon("plus", p.text))
        self.btn_folder.setIcon(theme.icon("folder", p.text))
        self.btn_return.setIcon(theme.icon("home", p.accent_text))
        self.btn_close.setIcon(theme.icon("x-circle", p.text))

    def set_current(self, name: Optional[str]) -> None:
        """Show (or hide) the card that leads back to the project that is open while the user is on this page."""
        self.current_card.setVisible(bool(name))
        self.current_name.setText(name or "")

    def set_recents(self, paths: list[str]) -> None:
        while self.recent_box.count():
            item = self.recent_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for path in paths[:prefs.get("recent_limit")]:
            b = QPushButton(f"{Path(path).name}\n{Path(path).parent}")
            b.setObjectName("RecentItem")
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(path)
            b.clicked.connect(lambda _c=False, p=path: self.open_recent.emit(p))
            self.recent_box.addWidget(b)
        self.recent_card.setVisible(bool(paths))
