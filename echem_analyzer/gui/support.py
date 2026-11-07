"""Author links, the coffee button and the support card, shared by the toolbar, the start page and the About window.

Every link opens in the user's browser and only ever comes from the constants in :mod:`echem_analyzer.about`.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget,
)

from .. import about, theme


def open_link(url: str) -> bool:
    """Open a web link in the user's browser."""
    return QDesktopServices.openUrl(QUrl(url))


class LinkButton(QPushButton):
    """A button with a theme icon (re-tinted when the theme changes) that opens a link.

    ``name`` is the style-sheet object name: ``Chip`` (icon and label), ``IconLink`` (icon only) or ``Coffee``.
    """

    def __init__(self, link: about.Link, text: Optional[str] = None, name: str = "Chip", icon_px: int = 16,
                 open_on_click: bool = True, parent: Optional[QWidget] = None):
        label = link.label if text is None else text
        super().__init__(f"  {label}" if label else "", parent)          # two spaces: the icon-to-text gap used across the app
        self.link = link
        self._icon_px = icon_px
        self.setObjectName(name)
        self.setCursor(Qt.PointingHandCursor)
        self.setIconSize(QSize(icon_px, icon_px))
        self.setToolTip(f"{link.label}\n{link.url}")
        self.setAccessibleName(link.label)
        if open_on_click:
            self.clicked.connect(lambda _checked=False: open_link(self.link.url))
        theme.bus.changed.connect(self._restyle)
        self._restyle()

    def _restyle(self) -> None:
        p = theme.current()
        color = about.COFFEE_FG if self.objectName() == "Coffee" else (p.muted if self.objectName() == "IconLink" else p.text)
        self.setIcon(theme.icon(self.link.icon, color, self._icon_px))


def coffee_button(compact: bool = False, open_on_click: bool = True, parent: Optional[QWidget] = None) -> LinkButton:
    """The Buy Me a Coffee button, in the service's own yellow so it is recognisable in light and dark."""
    b = LinkButton(about.COFFEE, name="Coffee", open_on_click=open_on_click, parent=parent)
    b.setProperty("compact", compact)
    b.setMinimumHeight(30 if compact else 36)
    return b


def social_chips(parent: Optional[QWidget] = None) -> QWidget:
    """Website, GitHub, LinkedIn and Instagram as labelled chips."""
    box = QWidget(parent)
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    for link in about.SOCIALS:
        row.addWidget(LinkButton(link))
    row.addStretch(1)
    return box


class Avatar(QWidget):
    """A round monogram: the author's initials on the app's accent gradient."""

    def __init__(self, initials: str, size: int = 56, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._initials = initials
        self.setFixedSize(size, size)

    def paintEvent(self, _e) -> None:  # noqa: N802
        pt = QPainter(self)
        pt.setRenderHint(QPainter.Antialiasing)
        g = QLinearGradient(0, 0, self.width(), self.height())
        g.setColorAt(0, QColor("#3B82F6"))
        g.setColorAt(1, QColor("#1D4ED8"))
        pt.setPen(Qt.NoPen)
        pt.setBrush(g)
        pt.drawEllipse(self.rect())
        f = QFont(self.font())
        f.setBold(True)
        f.setPixelSize(int(self.height() * 0.36))
        pt.setFont(f)
        pt.setPen(QColor("#FFFFFF"))
        pt.drawText(self.rect(), Qt.AlignCenter, self._initials)


class Hero(QWidget):
    """The About window's banner: a deep-blue gradient with soft light discs. Children are placed by the caller."""

    def paintEvent(self, _e) -> None:  # noqa: N802
        pt = QPainter(self)
        pt.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        g = QLinearGradient(0, 0, w, h)
        g.setColorAt(0, QColor("#0B1B3F"))
        g.setColorAt(0.55, QColor("#1736A8"))
        g.setColorAt(1, QColor("#2563EB"))
        pt.fillRect(self.rect(), g)
        pt.setPen(Qt.NoPen)
        for cx, cy, r, alpha in ((w * 0.86, h * 0.10, h * 0.95, 34), (w * 1.0, h * 1.05, h * 0.8, 26), (w * 0.62, h * 1.2, h * 0.55, 18)):
            pt.setBrush(QColor(255, 255, 255, alpha))
            pt.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
        # a faint pulse line along the bottom edge, echoing the app icon
        pen = QPen(QColor(255, 255, 255, 46), 1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        pt.setPen(pen)
        pt.setBrush(Qt.NoBrush)
        y = h - 22
        pts = [(0, y), (w * 0.52, y), (w * 0.55, y - 4), (w * 0.585, y + 12), (w * 0.625, y - 26), (w * 0.66, y + 8), (w * 0.69, y), (w, y)]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            pt.drawLine(int(x1), int(y1), int(x2), int(y2))


class Signature(QWidget):
    """"Made with <blue heart> by <author>".

    The heart is a filled shape from the icon set in the theme's blue, not an emoji, so it looks the same on every system and
    follows light and dark."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        lead = QLabel("Made with")
        lead.setObjectName("Signature")
        self.heart = QLabel()
        self.heart.setAlignment(Qt.AlignCenter)
        self.heart.setFixedSize(16, 16)
        tail = QLabel(f"by <b>{about.AUTHOR}</b>")
        tail.setObjectName("Signature")
        tail.setTextFormat(Qt.RichText)
        for w in (lead, self.heart, tail):
            row.addWidget(w)
        self.setAccessibleName(f"Made with love by {about.AUTHOR}")
        theme.bus.changed.connect(self._restyle)
        self._restyle()

    def plain_text(self) -> str:
        return f"Made with ♥ by {about.AUTHOR}"

    def _restyle(self) -> None:
        self.heart.setPixmap(theme.pixmap("heart-fill", theme.current().accent, 15))


class AuthorStrip(QWidget):
    """The signature and the author's links as icons, centred on one line: the foot of the start page.

    (The coffee button is in the toolbar, always visible, so it is not repeated here.)"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        row.addStretch(1)
        self.signature = Signature()
        row.addWidget(self.signature)
        row.addSpacing(10)
        for link in about.SOCIALS:
            row.addWidget(LinkButton(link, text="", name="IconLink", icon_px=18))
        row.addStretch(1)


class SupportCard(QFrame):
    """A small dismissible card that floats at the bottom-right of the window and links to Buy Me a Coffee.

    It never takes focus or blocks the window.  ``dismissed`` fires when the user closes it; ``never_show`` when they ask not
    to see it at startup (the window then stores that in the preferences).
    """

    WIDTH = 372
    dismissed = Signal()
    never_show = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("SupportCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(self.WIDTH)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(34)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 70))
        self.setGraphicsEffect(shadow)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 12, 12)
        outer.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(12)
        self._chip = QLabel()
        self._chip.setObjectName("CoffeeChip")
        self._chip.setFixedSize(36, 36)
        self._chip.setAlignment(Qt.AlignCenter)
        top.addWidget(self._chip, 0, Qt.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(3)
        title = QLabel("Enjoying Echem Analyzer?")
        title.setObjectName("SupportTitle")
        body = QLabel(f"Made by {about.AUTHOR}: free, and built in spare time. "
                      "If it saved you an afternoon at the bench, a coffee keeps it going.")
        body.setObjectName("SupportText")
        body.setWordWrap(True)
        text.addWidget(title)
        text.addWidget(body)
        top.addLayout(text, 1)
        self._close = QPushButton()
        self._close.setObjectName("CloseX")
        self._close.setCursor(Qt.PointingHandCursor)
        self._close.setToolTip("Close")
        self._close.setAccessibleName("Close")
        self._close.setIconSize(QSize(14, 14))
        self._close.clicked.connect(self.close_card)
        top.addWidget(self._close, 0, Qt.AlignTop)
        outer.addLayout(top)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(48, 0, 0, 0)               # lined up with the text, past the chip
        buttons.setSpacing(8)
        self.coffee = coffee_button(compact=True)
        self.coffee.clicked.connect(self.close_card)          # they acted on it: get out of the way
        later = QPushButton("Maybe later")
        later.setProperty("flat", True)
        later.setCursor(Qt.PointingHandCursor)
        later.clicked.connect(self.close_card)
        buttons.addWidget(self.coffee)
        buttons.addWidget(later)
        buttons.addStretch(1)
        outer.addLayout(buttons)

        quiet = QHBoxLayout()
        quiet.setContentsMargins(48, 0, 0, 0)
        never = QPushButton("Don’t show this at startup")
        never.setObjectName("QuietLink")
        never.setCursor(Qt.PointingHandCursor)
        never.clicked.connect(self._never)
        quiet.addWidget(never)
        quiet.addStretch(1)
        outer.addLayout(quiet)

        self._anim: Optional[QPropertyAnimation] = None
        theme.bus.changed.connect(self._restyle)
        self._restyle()
        self.hide()

    def _restyle(self) -> None:
        self._chip.setPixmap(theme.pixmap("coffee", about.COFFEE_FG, 20))
        self._close.setIcon(theme.icon("x", theme.current().muted, 14))

    # ------------------------------------------------------------------ placement
    def _target(self) -> QPoint:
        par = self.parentWidget()
        inset = 0
        if isinstance(par, QMainWindow) and par.statusBar() is not None and par.statusBar().isVisible():
            inset = par.statusBar().height()
        return QPoint(par.width() - self.width() - 22, par.height() - inset - self.height() - 20)

    def _fit(self) -> None:
        self.resize(self.WIDTH, max(self.sizeHint().height(), self.layout().totalHeightForWidth(self.WIDTH)))

    def reposition(self) -> None:
        """Keep the card in the corner when the window is resized."""
        if self.isVisible():
            if self._anim is not None:
                self._anim.stop()
            self._fit()
            self.move(self._target())

    def pop(self, animate: bool = True) -> None:
        """Show the card, sliding it up from the corner."""
        self._fit()
        end = self._target()
        self.show()
        self.raise_()
        if not animate:
            self.move(end)
            return
        self.move(end + QPoint(0, 18))
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(260)
        self._anim.setStartValue(end + QPoint(0, 18))
        self._anim.setEndValue(end)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.start()

    def toggle(self) -> None:
        self.close_card() if self.isVisible() else self.pop()

    def close_card(self) -> None:
        if self._anim is not None:
            self._anim.stop()
        was_visible = self.isVisible()
        self.hide()
        if was_visible:
            self.dismissed.emit()

    def _never(self) -> None:
        self.never_show.emit()
        self.close_card()
