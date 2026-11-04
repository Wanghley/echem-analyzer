"""The guide: a searchable window with the methods, formulas, shortcuts and limits of every analysis."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import help_content, theme
from ..palette import Palette


def guide_css(p: Palette) -> str:
    return (f"body {{ color:{p.text}; }} h2 {{ color:{p.text}; margin-bottom:4px; }} h3 {{ color:{p.muted}; margin-top:14px; }} "
            f"a {{ color:{p.accent}; }} code {{ background:{p.surface2}; color:{p.text}; }} "
            f"th {{ background:{p.surface2}; color:{p.muted}; }} td {{ border-bottom:1px solid {p.border}; }} "
            f"li {{ margin-bottom:4px; }} p {{ line-height:135%; }}")


class HelpDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None, topic_id: str = "start"):
        super().__init__(parent)
        self.setWindowTitle("Echem Analyzer: guide")
        self.resize(1000, 700)
        self._current = topic_id
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)

        left = QVBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the guide…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        left.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setObjectName("NavList")
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(12)
        self.tree.setMinimumWidth(240)
        self.tree.setMaximumWidth(300)
        self.tree.currentItemChanged.connect(self._selected)
        left.addWidget(self.tree, 1)
        root.addLayout(left)

        self.view = QTextBrowser()
        self.view.setObjectName("GuideView")
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._link)
        root.addWidget(self.view, 1)
        theme.bus.changed.connect(self._render)
        self._fill(help_content.TOPICS)
        self.show_topic(topic_id)

    # -- navigation
    def _fill(self, topics: list[help_content.Topic]) -> None:
        keep = self._current
        self.tree.blockSignals(True)
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for t in topics:
            parent = groups.get(t.group)
            if parent is None:
                parent = QTreeWidgetItem([t.group.upper()])
                parent.setFlags(Qt.ItemIsEnabled)
                self.tree.addTopLevelItem(parent)
                groups[t.group] = parent
            child = QTreeWidgetItem([t.title])
            child.setSizeHint(0, QSize(0, 28))
            child.setData(0, Qt.UserRole, t.id)
            parent.addChild(child)
        self.tree.expandAll()
        self.tree.blockSignals(False)
        self._select(keep)

    def _select(self, topic_id: str) -> None:
        it = self.tree.invisibleRootItem()
        first = None
        for g in range(it.childCount()):
            for c in range(it.child(g).childCount()):
                child = it.child(g).child(c)
                first = first or child
                if child.data(0, Qt.UserRole) == topic_id:
                    self.tree.setCurrentItem(child)
                    return
        if first is not None:
            self.tree.setCurrentItem(first)
        else:
            self.view.setHtml("<p>No topic matches your search.</p>")

    def _filter(self, text: str) -> None:
        self._fill(help_content.search(text))

    def _selected(self, item: Optional[QTreeWidgetItem]) -> None:
        tid = item.data(0, Qt.UserRole) if item is not None else None
        if tid:
            self._current = tid
            self._render()

    def _link(self, url: QUrl) -> None:
        if url.scheme() == "topic":
            self.search.clear()
            self.show_topic(url.path())

    def show_topic(self, topic_id: str) -> None:
        if topic_id not in {t.id for t in help_content.TOPICS}:
            topic_id = "start"
        self._current = topic_id
        self._select(topic_id)
        self._render()

    def _render(self) -> None:
        try:
            t = help_content.topic(self._current)
        except KeyError:
            return
        self.view.document().setDefaultStyleSheet(guide_css(theme.current()))
        self.view.setHtml(t.html)
        self.view.verticalScrollBar().setValue(0)

    @property
    def current_topic(self) -> str:
        return self._current
