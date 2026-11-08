"""The "Updates" card of the About window: shows the update manager's state and offers the one next step."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QProgressBar, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from .. import theme, updater
from ..prefs import prefs
from ..timefmt import human_bytes, humanize_age
from .support import open_link
from .ui_kit import Card, repolish
from .update_manager import State, UpdateManager

# state -> (tone, icon)
LOOK = {
    State.IDLE: ("neutral", "refresh-cw"), State.CHECKING: ("neutral", "refresh-cw"), State.UP_TO_DATE: ("success", "check-circle"),
    State.AVAILABLE: ("accent", "arrow-up-circle"), State.DOWNLOADING: ("accent", "download"), State.VERIFYING: ("accent", "download"),
    State.READY: ("success", "arrow-up-circle"), State.INSTALLING: ("accent", "arrow-up-circle"), State.ERROR: ("danger", "alert-triangle"),
}
TONE_COLOR = {"neutral": "muted", "accent": "accent", "success": "success", "danger": "danger"}


class UpdatePanel(Card):
    def __init__(self, manager: UpdateManager, parent: Optional[QWidget] = None):
        super().__init__("Updates", parent, margins=16)
        self.manager = manager

        top = QHBoxLayout()
        top.setSpacing(12)
        self.chip = QLabel()
        self.chip.setObjectName("UpdateChip")
        self.chip.setProperty("tone", "neutral")
        self.chip.setFixedSize(36, 36)
        self.chip.setAlignment(Qt.AlignCenter)
        top.addWidget(self.chip, 0, Qt.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = QLabel()
        self.title.setObjectName("UpdateTitle")
        self.detail = QLabel()
        self.detail.setObjectName("UpdateDetail")
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        text.addWidget(self.title)
        text.addWidget(self.detail)
        top.addLayout(text, 1)
        self.body.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setVisible(False)
        self.body.addWidget(self.bar)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.main = QPushButton()
        self.main.setMinimumHeight(34)
        self.main.setMinimumWidth(170)
        self.main.setCursor(Qt.PointingHandCursor)
        self.main.setIconSize(QSize(16, 16))
        self.main.clicked.connect(self._main_clicked)
        self.second = QPushButton("Release page")
        self.second.setMinimumHeight(34)
        self.second.setCursor(Qt.PointingHandCursor)
        self.second.clicked.connect(self._second_clicked)
        buttons.addWidget(self.main)
        buttons.addWidget(self.second)
        buttons.addStretch(1)
        self.body.addLayout(buttons)

        self.notes_toggle = QPushButton()
        self.notes_toggle.setObjectName("CardToggle")
        self.notes_toggle.setFlat(True)
        self.notes_toggle.setCursor(Qt.PointingHandCursor)
        self.notes_toggle.clicked.connect(lambda: self._show_notes(not self.notes.isVisible()))
        self.notes = QTextBrowser()
        self.notes.setObjectName("Notes")
        self.notes.setOpenLinks(False)                       # release notes come from the network: nothing in them opens by itself
        self.notes.setOpenExternalLinks(False)
        self.notes.anchorClicked.connect(self._note_link)
        self.notes.setMaximumHeight(190)
        self.notes.setVisible(False)
        self.body.addWidget(self.notes_toggle)
        self.body.addWidget(self.notes)

        self.auto = QCheckBox("Check for updates automatically when the app starts (at most once a day)")
        self.auto.setChecked(bool(prefs.get("update_check")))
        self.auto.toggled.connect(lambda on: prefs.set("update_check", bool(on)))
        self.body.addWidget(self.auto)

        manager.state_changed.connect(self.render)
        manager.progress_changed.connect(self._render_progress)
        theme.bus.changed.connect(self.render)
        self._notes_for = ""
        self.render()

    # ------------------------------------------------------------------ rendering
    def render(self) -> None:
        m, st = self.manager, self.manager.state
        cur, rel = updater.__version__, m.release
        v = rel.text if rel else ""
        tone, icon = LOOK[st]
        title, detail, main, primary, enabled = self._words(st, m, cur, v)
        self.title.setText(title)
        self.detail.setText(detail)
        self.chip.setProperty("tone", tone)
        repolish(self.chip)
        color = getattr(theme.current(), TONE_COLOR[tone])
        self.chip.setPixmap(theme.pixmap(icon, color, 20))
        self.main.setText(f"  {main}")
        self.main.setProperty("primary", primary)
        self.main.setEnabled(enabled)
        repolish(self.main)
        self.main.setIcon(theme.icon(self._main_icon(st, m), theme.current().accent_text if primary else theme.current().text, 16))
        self.second.setVisible(st is State.ERROR or (bool(rel) and st in (State.AVAILABLE, State.READY)))      # after a failure: the page is the way out
        showing_bar = st in (State.CHECKING, State.DOWNLOADING, State.VERIFYING, State.INSTALLING)
        self.bar.setVisible(showing_bar)
        if showing_bar:
            self._render_progress(m.done, m.total)
        self._render_notes(st, rel)

    def _words(self, st, m, cur, v):
        """(title, detail, main button text, main is primary, main enabled) for a state."""
        if st is State.IDLE:
            seen = m.hint_version()
            ago = f"Last checked {humanize_age(m.last_checked)}." if m.last_checked else "Not checked yet."
            return ("Check for updates", f"You have version {cur}. {ago}", "Check for updates", seen is None, True) if not seen else \
                   (f"Version {seen} is available", "Check now to download it.", "Check now", True, True)
        if st is State.CHECKING:
            return "Checking for updates…", "Asking GitHub for the newest release.", "Checking…", False, False
        if st is State.UP_TO_DATE:
            return ("You are up to date", f"Version {cur} is the newest release. Last checked {humanize_age(m.last_checked)}.",
                    "Check again", False, True)
        if st is State.AVAILABLE:
            size = f" · {human_bytes(m.asset.size)} download" if m.asset else ""
            when = f"Released {m.release.published}" if m.release.published else "A new release"
            if m.can_auto_install:
                return f"Version {v} is available", f"{when}{size}. You have {cur}.", "Download and install", True, True
            return f"Version {v} is available", f"{when}. You have {cur}. {m.manual_install_note()}", "Open the download page", True, True
        if st is State.DOWNLOADING:
            return f"Downloading version {v}…", self._progress_text(m), "Cancel", False, True
        if st is State.VERIFYING:
            return f"Verifying version {v}…", "Checking the download against its published SHA-256 checksum.", "Verifying…", False, False
        if st is State.READY:
            return (f"Version {v} is ready to install",
                    "Echem Analyzer will close, install the update and open again. If your project has unsaved changes you are asked first.",
                    "Install and restart", True, True)
        if st is State.INSTALLING:
            return "Installing the update…", "Echem Analyzer is closing so the update can finish. It opens again by itself.", "Installing…", False, False
        return "The update did not finish", m.error or "Something went wrong.", ("Try again" if m.release else "Check again"), True, True

    @staticmethod
    def _main_icon(st, m) -> str:
        return {State.AVAILABLE: "download" if m.can_auto_install else "external-link", State.READY: "arrow-up-circle",
                State.DOWNLOADING: "x", State.ERROR: "refresh-cw"}.get(st, "refresh-cw")

    @staticmethod
    def _progress_text(m) -> str:
        return f"{human_bytes(m.done)} of {human_bytes(m.total)}" if m.total else "Starting…"

    def _render_progress(self, done: int, total: int) -> None:
        st = self.manager.state
        if st is State.DOWNLOADING and total > 0:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(1000 * done / total))
            self.detail.setText(self._progress_text(self.manager))
        elif st in (State.DOWNLOADING, State.CHECKING, State.VERIFYING, State.INSTALLING):
            self.bar.setRange(0, 0)                          # no known length: the busy animation

    def _render_notes(self, st: State, rel) -> None:
        show = bool(rel and rel.notes.strip()) and st in (State.AVAILABLE, State.DOWNLOADING, State.VERIFYING, State.READY, State.ERROR)
        self.notes_toggle.setVisible(show)
        if not show:
            self.notes.setVisible(False)
            return
        if self._notes_for != rel.text:
            self._notes_for = rel.text
            # Markdown without raw HTML: the text is untrusted, so tags in it are shown as text and never act
            self.notes.document().setMarkdown(
                rel.notes, QTextDocument.MarkdownFeature.MarkdownDialectGitHub | QTextDocument.MarkdownFeature.MarkdownNoHTML)
            self.notes.setVisible(False)
        self._show_notes(self.notes.isVisible())

    def _show_notes(self, on: bool) -> None:
        rel = self.manager.release
        self.notes.setVisible(on)
        self.notes_toggle.setText(f"{'▾' if on else '▸'}  WHAT’S NEW IN {rel.text if rel else ''}")

    # ------------------------------------------------------------------ actions
    def _main_clicked(self) -> None:
        m, st = self.manager, self.manager.state
        if st is State.AVAILABLE:
            if m.can_auto_install:
                m.download()
            elif m.release:
                open_link(m.release.page)
        elif st is State.DOWNLOADING:
            m.cancel()
        elif st is State.READY:
            m.request_install()
        elif st is State.ERROR:
            m.retry()
        elif st in (State.IDLE, State.UP_TO_DATE):
            m.check(manual=True)

    def _second_clicked(self) -> None:
        open_link(self.manager.release.page if self.manager.release else updater.RELEASES_PAGE)

    def _note_link(self, url: QUrl) -> None:
        if url.scheme() == "https":                          # only ever web links, and only when the user clicks one
            open_link(url.toString())
