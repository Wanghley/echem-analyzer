"""The start page: one quiet, centred column. A clear first step, the topics the app handles, and where to learn more.

Recent projects are not here: they are under *File ▸ Open recent*, and an open project shows in the panel on the left.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from .. import __version__, theme
from .support import AuthorStrip
from .ui_kit import Card

# (title, one line, guide topic, icon, short name shown on the tile): each tile opens the matching part of the guide
TILES = [
    ("Calibration and LOD", "Sensitivity, linear range, LOD and LOQ from peaks, currents or impedance", "calibration", "bar-chart", "Calibration"),
    ("Voltammetry", "CV cycles, DPV / SWV / LSV peaks, baselines and widths", "peaks", "repeat", "Voltammetry"),
    ("Impedance", "Equivalent-circuit fits, Rct, Nyquist and Bode plots", "eis", "activity", "Impedance"),
    ("Potentiometry and OCP", "Steady state, drift, response time, Nernstian slope", "potentiometry", "zap", "Potentiometry"),
    ("Reproducibility and stability", "RSD between electrodes, retention over time, selectivity, recovery", "repro", "layers", "Reproducibility"),
    ("Electrode characterisation", "Electroactive area, roughness, double-layer capacitance, kinetics", "electrode", "sliders", "Electrodes"),
]

COLUMN_WIDTH = 540          # the content is one column this wide, centred, however large the window


def _native(seq: str) -> str:
    """A shortcut as the platform writes it (⌘O on macOS, Ctrl+O elsewhere)."""
    return QKeySequence(seq).toString(QKeySequence.NativeText)


class _Tile(QPushButton):
    """A topic: a small icon and a short name. The full title and description are in the tooltip."""

    def __init__(self, title: str, sub: str, icon: str, short: str):
        super().__init__()
        self.setObjectName("Tile")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(46)
        self.setToolTip(f"{title}\n{sub}")
        self._icon = icon
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 0, 14, 0)
        row.setSpacing(9)
        self.chip = QLabel()
        self.chip.setObjectName("TileGlyph")
        self.chip.setFixedSize(20, 20)
        self.chip.setAlignment(Qt.AlignCenter)
        self.chip.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self.chip)
        self.name = QLabel(short)
        self.name.setObjectName("TileName")
        self.name.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self.name, 1)
        theme.bus.changed.connect(self.restyle)
        self.restyle()

    def restyle(self) -> None:
        self.chip.setPixmap(theme.pixmap(self._icon, theme.current().accent, 18))


class _ActionRow(QPushButton):
    """A first-step button: icon, title, one line of help and the keyboard shortcut. ``primary`` fills it with the accent."""

    def __init__(self, title: str, sub: str, icon: str, shortcut: str = "", primary: bool = False):
        super().__init__()
        self.setObjectName("ActionRow")
        self.setProperty("primary", primary)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(58)
        self._icon, self._primary = icon, primary
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 14, 8)
        row.setSpacing(13)
        self.chip = QLabel()
        self.chip.setObjectName("ActionIcon")
        self.chip.setFixedSize(38, 38)
        self.chip.setAlignment(Qt.AlignCenter)
        self.chip.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self.chip)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("ActionTitle")
        sub_label = QLabel(sub)
        sub_label.setObjectName("ActionSub")
        for lab in (self.title_label, sub_label):
            lab.setAttribute(Qt.WA_TransparentForMouseEvents)
            col.addWidget(lab)
        row.addLayout(col, 1)
        self.keycap: Optional[QLabel] = None
        if shortcut:
            self.keycap = QLabel(shortcut)
            self.keycap.setObjectName("KeyCap")
            self.keycap.setAttribute(Qt.WA_TransparentForMouseEvents)
            row.addWidget(self.keycap, 0, Qt.AlignVCenter)
        theme.bus.changed.connect(self.restyle)
        self.restyle()

    def restyle(self) -> None:
        p = theme.current()
        self.chip.setPixmap(theme.pixmap(self._icon, p.accent_text if self._primary else p.accent, 20))


class WelcomePage(QWidget):
    open_project = Signal()
    add_files = Signal()
    add_folder = Signal()
    return_to_project = Signal()
    close_project = Signal()
    open_help = Signal(str)
    open_settings = Signal()
    open_about = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.viewport().setAutoFillBackground(False)         # the window colour shows through
        page = QWidget()
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(24, 24, 24, 24)
        self.column = self._build_column()
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.column, 100)
        row.addStretch(1)
        page_lay.addStretch(5)                                      # a little more room above than below: it reads as centred
        page_lay.addLayout(row)
        page_lay.addStretch(6)
        self.scroll.setWidget(page)
        outer.addWidget(self.scroll, 1)

        foot = QFrame()
        foot.setObjectName("PageFooter")
        foot.setAttribute(Qt.WA_StyledBackground, True)
        foot_lay = QVBoxLayout(foot)
        foot_lay.setContentsMargins(24, 10, 24, 10)
        self.strip = AuthorStrip()
        foot_lay.addWidget(self.strip)
        outer.addWidget(foot)

        theme.bus.changed.connect(self.restyle)
        self.restyle()

    # ------------------------------------------------------------------ pieces
    def _build_column(self) -> QWidget:
        col = QWidget()
        col.setMaximumWidth(COLUMN_WIDTH)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.logo = QLabel()
        self.logo.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.logo)
        title = QLabel("Echem Analyzer")
        title.setObjectName("WelcomeTitle")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)
        tagline = QLabel("Sensor electrochemistry, from PalmSens and other instruments")
        tagline.setObjectName("WelcomeSub")
        tagline.setAlignment(Qt.AlignCenter)
        tagline.setWordWrap(True)
        lay.addWidget(tagline)

        meta = QHBoxLayout()
        meta.setSpacing(8)
        meta.addStretch(1)
        self.version_badge = QLabel(f"v{__version__}")
        self.version_badge.setObjectName("VersionTag")
        meta.addWidget(self.version_badge)
        self.update_pill = QPushButton()
        self.update_pill.setObjectName("UpdateBadge")
        self.update_pill.setCursor(Qt.PointingHandCursor)
        self.update_pill.setIconSize(QSize(16, 16))
        self.update_pill.setToolTip("Open the About window to download and install the update")
        self.update_pill.clicked.connect(self.open_about)
        self.update_pill.setVisible(False)
        meta.addWidget(self.update_pill)
        meta.addStretch(1)
        lay.addLayout(meta)
        lay.addSpacing(18)

        self.current_card = Card("Current project", margins=14)
        self.current_name = QLabel("")
        self.current_name.setObjectName("WelcomeSub")
        self.current_card.body.addWidget(self.current_name)
        cur_row = QHBoxLayout()
        cur_row.setSpacing(8)
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
        lay.addWidget(self.current_card)
        lay.addSpacing(4)

        self.btn_project = _ActionRow("Open a project", "Continue where you left off", "folder-open", _native("Ctrl+O"), primary=True)
        self.btn_files = _ActionRow("Add data files", "PalmSens .csv and .pssession exports", "plus", _native("Ctrl+Shift+O"))
        self.btn_folder = _ActionRow("Add a data folder", "Everything the app can read in a folder", "folder", _native("Ctrl+Alt+O"))
        for b in (self.btn_project, self.btn_files, self.btn_folder):
            lay.addWidget(b)
        self.btn_project.clicked.connect(self.open_project)
        self.btn_files.clicked.connect(self.add_files)
        self.btn_folder.clicked.connect(self.add_folder)

        self.drop_hint = QLabel("or drop  .csv  ·  .pssession  ·  .echemproj  files anywhere in this window")
        self.drop_hint.setObjectName("DropHint")
        self.drop_hint.setAlignment(Qt.AlignCenter)
        self.drop_hint.setWordWrap(True)
        lay.addSpacing(2)
        lay.addWidget(self.drop_hint)
        lay.addSpacing(22)

        topics = QLabel("LEARN BY TOPIC")
        topics.setObjectName("SectionLabel")
        topics.setAlignment(Qt.AlignCenter)
        lay.addWidget(topics)
        grid = QGridLayout()
        grid.setSpacing(8)
        for c in range(3):
            grid.setColumnStretch(c, 1)
        for k, (full, sub, topic, icon, short) in enumerate(TILES):
            tile = _Tile(full, sub, icon, short)
            tile.clicked.connect(lambda _c=False, t=topic: self.open_help.emit(t))
            grid.addWidget(tile, k // 3, k % 3)
        lay.addLayout(grid)
        lay.addSpacing(14)

        links = QHBoxLayout()
        links.setSpacing(4)
        links.addStretch(1)
        self.help_links: dict[str, QPushButton] = {}
        for text, fn in (("Guide", lambda: self.open_help.emit("start")), ("Keyboard shortcuts", lambda: self.open_help.emit("shortcuts")),
                         ("Settings", lambda: self.open_settings.emit()), ("About and updates", lambda: self.open_about.emit())):
            b = QPushButton(text)
            b.setObjectName("LinkButton")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, f=fn: f())
            links.addWidget(b)
            self.help_links[text] = b
        links.addStretch(1)
        lay.addLayout(links)
        return col

    # ------------------------------------------------------------------ behaviour
    def restyle(self) -> None:
        self.logo.setPixmap(theme.app_logo(72))
        p = theme.current()
        self.btn_return.setIcon(theme.icon("home", p.accent_text))
        self.btn_close.setIcon(theme.icon("x-circle", p.text))
        self.update_pill.setIcon(theme.icon("arrow-up-circle", p.accent, 16))

    def set_update(self, text: Optional[str]) -> None:
        """Show (or hide) the update notice under the version."""
        self.update_pill.setVisible(bool(text))
        self.update_pill.setText(f"  {text}" if text else "")

    def set_current(self, name: Optional[str]) -> None:
        """Show (or hide) the card that leads back to the project that is open while the user is on this page."""
        self.current_card.setVisible(bool(name))
        self.current_name.setText(name or "")
