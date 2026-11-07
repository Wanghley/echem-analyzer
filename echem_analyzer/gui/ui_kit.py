"""Small reusable widgets that give the app a consistent, polished look (styled by ``theme``)."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QSettings, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSplitter, QSplitterHandle, QVBoxLayout, QWidget,
)


def repolish(w: QWidget) -> None:
    """Re-apply the style sheet after a dynamic property (e.g. ``tone``) changed."""
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


class _Handle(QSplitterHandle):
    """A splitter handle you can see: a faint grip that lights up on hover. Double-click resets the sizes."""

    def __init__(self, orientation: Qt.Orientation, parent: "Splitter"):
        super().__init__(orientation, parent)
        self._hot = False
        self.setToolTip("Drag to resize  ·  double-click to reset")

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hot = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hot = False
        self.update()
        super().leaveEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        self.splitter().reset_sizes()

    def paintEvent(self, e) -> None:  # noqa: N802
        from .. import theme
        p = theme.current()
        pt = QPainter(self)
        pt.setRenderHint(QPainter.Antialiasing)
        hot = self._hot or bool(self.window().mouseGrabber() is self)
        if hot:
            pt.setPen(Qt.NoPen)
            pt.setBrush(QColor(p.accent_soft))
            pt.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 4, 4)
        pt.setPen(Qt.NoPen)
        pt.setBrush(QColor(p.accent if hot else p.border_strong))
        c = QPointF(self.width() / 2, self.height() / 2)
        along_y = self.orientation() == Qt.Horizontal          # a bar between left and right: stack the dots vertically
        for k in (-1, 0, 1):
            pt.drawEllipse(c + (QPointF(0, k * 6) if along_y else QPointF(k * 6, 0)), 1.7, 1.7)


class Splitter(QSplitter):
    """A QSplitter with a visible grip that remembers its sizes between sessions.

    ``remember(qs, key, default)`` restores the last sizes from the settings and saves them whenever the user drags a handle;
    double-clicking a handle goes back to the default sizes.
    """

    def __init__(self, orientation: Qt.Orientation, parent: Optional[QWidget] = None):
        super().__init__(orientation, parent)
        self.setHandleWidth(11)
        self.setChildrenCollapsible(False)
        self._qs: Optional[QSettings] = None
        self._key = ""
        self._default: Optional[list[int]] = None
        self._requested: Optional[list[int]] = None
        self._stretch: dict[int, int] = {}

    def setStretchFactor(self, index: int, stretch: int) -> None:  # noqa: N802
        self._stretch[index] = stretch                     # Qt 6 has no getter, and "reset" falls back on these
        super().setStretchFactor(index, stretch)

    def setSizes(self, sizes: list[int]) -> None:  # noqa: N802
        self._requested = [int(s) for s in sizes]          # remembered: sizes() is meaningless before the first layout
        super().setSizes(sizes)

    def createHandle(self) -> QSplitterHandle:  # noqa: N802
        return _Handle(self.orientation(), self)

    def remember(self, qs: QSettings, key: str, default: Optional[list[int]] = None) -> None:
        self._qs, self._key = qs, f"layout/{key}"
        sizes = default or self._requested or [s for s in self.sizes()]
        self._default = [int(s) for s in sizes] if sizes and sum(sizes) > 0 else None
        saved = self.saved_sizes()
        if saved:
            super().setSizes(saved)                        # keep the default for "reset", not the saved sizes
        self.splitterMoved.connect(lambda *_a: self._save())

    def saved_sizes(self) -> Optional[list[int]]:
        if self._qs is None:
            return None
        raw = self._qs.value(self._key)
        try:
            sizes = [int(float(v)) for v in (raw if isinstance(raw, (list, tuple)) else [raw])]
        except (TypeError, ValueError):
            return None
        return sizes if len(sizes) == self.count() and all(s > 0 for s in sizes) else None

    def _save(self) -> None:
        if self._qs is not None and all(s > 0 for s in self.sizes()):
            self._qs.setValue(self._key, [int(s) for s in self.sizes()])

    def reset_sizes(self) -> None:
        sizes = self._default or [max(self._stretch.get(i, 1), 1) * 100 for i in range(self.count())]
        super().setSizes(sizes)
        if self._qs is not None:
            self._qs.remove(self._key)


def persist_splitters(root: QWidget, qs: QSettings, prefix: str) -> None:
    """Make every Splitter below ``root`` remember its sizes (keyed by position, so the layout must not change order)."""
    for i, sp in enumerate(root.findChildren(Splitter)):
        sp.remember(qs, f"{prefix}/{i}")


class Card(QFrame):
    """A rounded surface with an optional small-caps title; put content into ``card.body``."""

    def __init__(self, title: str = "", parent: Optional[QWidget] = None, margins: int = 14):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setAttribute(Qt.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(margins, margins - 2, margins, margins)
        outer.setSpacing(8)
        self.title_label: Optional[QLabel] = None
        if title:
            self.title_label = QLabel(title.upper())
            self.title_label.setObjectName("CardTitle")
            outer.addWidget(self.title_label)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        self.body.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self.body)


class CollapsibleCard(Card):
    """A card whose content can be folded away: keeps rarely used settings out of the way."""

    def __init__(self, title: str, expanded: bool = False, parent: Optional[QWidget] = None):
        super().__init__("", parent)
        self._title = title.upper()
        self.toggle = QPushButton()                    # a push button, not a tool button: only it honours ``text-align: left``
        self.toggle.setObjectName("CardToggle")
        self.toggle.setCursor(Qt.PointingHandCursor)
        self.toggle.setFlat(True)
        self.toggle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.layout().insertWidget(0, self.toggle)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(8)
        self.body.addWidget(self.content)
        self.toggle.clicked.connect(lambda: self.set_expanded(not self.is_expanded()))
        self.set_expanded(expanded)

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, on: bool) -> None:
        self._expanded = on
        self.content.setVisible(on)
        self.toggle.setText(f"{'▾' if on else '▸'}  {self._title}")


class KpiCard(Card):
    """A headline figure: small label, large value, optional sub-line; the value can be tinted."""

    def __init__(self, label: str, parent: Optional[QWidget] = None):
        super().__init__("", parent, margins=12)
        self.label = QLabel(label.upper())
        self.label.setObjectName("KpiLabel")
        self.value = QLabel("—")
        self.value.setObjectName("KpiValue")
        self.sub = QLabel("")
        self.sub.setObjectName("KpiSub")
        self.sub.setWordWrap(True)
        for w in (self.label, self.value, self.sub):
            self.body.addWidget(w)
        self.body.setSpacing(1)
        self.setMinimumWidth(96)

    def set(self, value: str, sub: str = "", tone: str = "") -> None:
        self.value.setText(value)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))
        color = {"success": "success", "warning": "warning", "danger": "danger"}.get(tone)
        from .. import theme                                   # late: the palette may have changed
        p = theme.current()
        self.value.setStyleSheet(f"color: {getattr(p, color)};" if color else "")


class ElidedLabel(QLabel):
    """A label that cuts long text with an ellipsis instead of widening its layout (paths, long names).

    ``text()`` and ``full_text()`` are the whole string; ``elided_text()`` is what is drawn. Put the full text in a tooltip
    on the widget that holds the label."""

    def __init__(self, text: str = "", mode: Qt.TextElideMode = Qt.ElideMiddle, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._full, self._mode = text, mode
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)         # its width comes from the layout, not from the text
        self.setMinimumWidth(12)
        self._apply()

    def text(self) -> str:
        return self._full

    full_text = text

    def elided_text(self) -> str:
        return QLabel.text(self)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self._apply()

    set_full_text = setText

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._apply()

    def _apply(self) -> None:
        QLabel.setText(self, self.fontMetrics().elidedText(self._full, self._mode, max(self.width(), 12)))


class Badge(QLabel):
    def __init__(self, text: str = "", tone: str = "neutral", parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self.setObjectName("Badge")
        self.setAlignment(Qt.AlignCenter)
        self.set(text, tone)

    def set(self, text: str, tone: str = "neutral") -> None:
        self.setText(text)
        self.setProperty("tone", tone)
        repolish(self)


class Notice(QLabel):
    """A short inline message (warning / success / info) that wraps and can be hidden."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Notice")
        self.setWordWrap(True)
        self.setTextFormat(Qt.RichText)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setVisible(False)

    def show_message(self, html: str, tone: str = "warning") -> None:
        self.setText(html)
        self.setProperty("tone", tone)
        repolish(self)
        self.setVisible(True)


def hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("Hint")
    lab.setWordWrap(True)
    return lab


def kpi_row(*cards: KpiCard) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    for c in cards:
        lay.addWidget(c, 1)
    return w
