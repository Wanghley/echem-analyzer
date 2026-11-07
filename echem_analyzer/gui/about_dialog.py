"""The About window: updates, version information, who made the app, how to support it, and the methods in short."""
from __future__ import annotations

from typing import Optional

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import Qt, QTimer, qVersion
from PySide6.QtWidgets import (
    QApplication, QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from .. import __version__, about, theme, updater
from .support import Avatar, Hero, LinkButton, Signature, coffee_button, social_chips
from .ui_kit import Card, CollapsibleCard, ElidedLabel
from .update_manager import UpdateManager
from .update_panel import UpdatePanel

TAGLINE = "Sensor electrochemistry: calibration, voltammetry, impedance and potentiometry, from PalmSens and other instruments."

METHODS = (
    "<b>Projects</b> (.echemproj) hold the raw curves, every setting, the window state and the plots in one file. "
    "<b>Reports</b> (PDF) and <b>workbooks</b> (Excel) are generated from the same analysis you see on screen.<br><br>"
    "<b>Standard addition:</b> |i| of each plateau is fitted against C = C<sub>s</sub>·ΣV/(V<sub>0</sub>+ΣV); "
    "C<sub>x</sub> = intercept/slope; LOD = 3σ/S, LOQ = 10σ/S. Background current biases C<sub>x</sub> "
    "unless a blank is subtracted.<br><br>"
    "<b>Voltammetry:</b> anodic peak = maximum on the positive-going sweep, cathodic = minimum on the "
    "negative-going sweep; charge = ∫ i dt; apparent capacitance (Q<sub>a</sub> − Q<sub>c</sub>)/(2ΔE) "
    "also contains any Faradaic current."
)


def version_rows() -> list[tuple[str, str]]:
    """What is running, as label/value pairs: the app, how it is installed, the system, and the libraries."""
    rows = updater.build_info()
    at = next(i for i, (k, _v) in enumerate(rows) if k == "Python") + 1
    rows[at:at] = [("PySide6", PYSIDE_VERSION), ("Qt", qVersion())]
    return rows


def version_line() -> str:
    """One line that says exactly what is running."""
    d = dict(version_rows())
    return f"Version {d['Version']}  ·  Python {d['Python']}  ·  PySide6 {d['PySide6']}  ·  Qt {d['Qt']}"


def details_text() -> str:
    """The version information as plain text, for the clipboard (a bug report)."""
    return "Echem Analyzer\n" + "\n".join(f"{k}: {v}" for k, v in version_rows())


class AboutDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None, updates: Optional[UpdateManager] = None):
        super().__init__(parent)
        self.setObjectName("AboutDialog")
        self.setWindowTitle("About Echem Analyzer")
        self.setMinimumWidth(640)
        self.resize(680, 780)
        self.updates = updates or UpdateManager(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- banner
        hero = Hero()
        hero.setFixedHeight(150)
        row = QHBoxLayout(hero)
        row.setContentsMargins(28, 20, 28, 20)
        row.setSpacing(20)
        self.logo = QLabel()
        self.logo.setObjectName("HeroLogo")
        self.logo.setAlignment(Qt.AlignCenter)
        self.logo.setFixedSize(92, 92)
        row.addWidget(self.logo, 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(5)
        col.addStretch(1)
        title = QLabel("Echem Analyzer")
        title.setObjectName("HeroTitle")
        sub = QLabel(TAGLINE)
        sub.setObjectName("HeroSub")
        sub.setWordWrap(True)
        badges = QHBoxLayout()
        self.version_badge = QLabel(f"v{__version__}")
        self.version_badge.setObjectName("HeroBadge")
        badges.addWidget(self.version_badge)
        badges.addStretch(1)
        col.addWidget(title)
        col.addWidget(sub)
        col.addSpacing(4)
        col.addLayout(badges)
        col.addStretch(1)
        row.addLayout(col, 1)
        root.addWidget(hero)

        # ---- body (scrolls when the window is short)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(22, 18, 22, 12)
        lay.setSpacing(14)

        self.update_panel = UpdatePanel(self.updates)
        lay.addWidget(self.update_panel)
        lay.addWidget(self._version_card())

        author = Card("Author", margins=16)
        top = QHBoxLayout()
        top.setSpacing(14)
        top.addWidget(Avatar(about.AUTHOR_INITIALS, 58), 0, Qt.AlignTop)
        who = QVBoxLayout()
        who.setSpacing(1)
        name = QLabel(about.AUTHOR)
        name.setObjectName("AuthorName")
        name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        role = QLabel(about.AUTHOR_ROLE)
        role.setObjectName("AuthorRole")
        who.addWidget(name)
        who.addWidget(role)
        who.addStretch(1)
        top.addLayout(who, 1)
        author.body.addLayout(top)
        author.body.addSpacing(4)
        author.body.addWidget(social_chips())
        lay.addWidget(author)

        support = Card("Support the project", margins=16)
        pitch = QLabel(about.PITCH)
        pitch.setObjectName("SettingsHint")
        pitch.setWordWrap(True)
        support.body.addWidget(pitch)
        support_row = QHBoxLayout()
        support_row.addWidget(coffee_button())
        support_row.addStretch(1)
        support.body.addLayout(support_row)
        lay.addWidget(support)

        methods = CollapsibleCard("Methods at a glance", expanded=False)
        text = QLabel(METHODS)
        text.setObjectName("SettingsHint")
        text.setWordWrap(True)
        text.setTextFormat(Qt.RichText)
        methods.content_layout.addWidget(text)
        lay.addWidget(methods)
        lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # ---- footer
        foot = QHBoxLayout()
        foot.setContentsMargins(24, 10, 22, 16)
        foot.setSpacing(10)
        self.signature = Signature()
        foot.addWidget(self.signature)
        foot.addStretch(1)
        foot.addWidget(LinkButton(about.PROJECT, text="Source and releases"))
        close = QPushButton("Close")
        close.setProperty("primary", True)
        close.setMinimumWidth(92)
        close.setMinimumHeight(32)
        close.setDefault(True)
        close.clicked.connect(self.accept)
        foot.addWidget(close)
        root.addLayout(foot)

        theme.bus.changed.connect(self._restyle)
        self._restyle()

    def _version_card(self) -> Card:
        card = Card("Version information", margins=16)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        self.info_values: dict[str, QLabel] = {}
        for i, (key, value) in enumerate(version_rows()):
            k = QLabel(key)
            k.setObjectName("InfoKey")
            if key == "Location":
                v = ElidedLabel(value, Qt.ElideMiddle)
                v.setToolTip(value)
            else:
                v = QLabel(value)
            v.setObjectName("InfoValue")
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(k, i, 0, Qt.AlignTop)
            grid.addWidget(v, i, 1)
            self.info_values[key] = v
        card.body.addLayout(grid)
        row = QHBoxLayout()
        self.copy_btn = QPushButton("  Copy details")
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.setToolTip("Copy the version information to the clipboard, for a bug report")
        self.copy_btn.clicked.connect(self._copy)
        self._copy_timer = QTimer(self)                      # a child of the dialog: it can never fire after the dialog is gone
        self._copy_timer.setSingleShot(True)
        self._copy_timer.timeout.connect(lambda: self.copy_btn.setText("  Copy details"))
        row.addWidget(self.copy_btn)
        row.addStretch(1)
        card.body.addLayout(row)
        return card

    def _copy(self) -> None:
        QApplication.clipboard().setText(details_text())
        self.copy_btn.setText("  Copied")
        self._copy_timer.start(1800)

    def _restyle(self) -> None:
        self.logo.setPixmap(theme.app_logo(64))
        self.copy_btn.setIcon(theme.icon("copy", theme.current().text, 16))
