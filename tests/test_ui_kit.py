"""Small reusable widgets: the folded card header and the elided label.
"""

import re

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from echem_analyzer import theme
from echem_analyzer.gui.ui_kit import CollapsibleCard, ElidedLabel


def test_a_folded_card_header_is_a_push_button_so_its_text_can_be_left_aligned(qapp):
    card = CollapsibleCard("Advanced", expanded=False)
    assert isinstance(card.toggle, QPushButton) and card.toggle.isFlat()           # a tool button ignores text-align
    for mode in ("light", "dark"):
        sheet = theme.stylesheet(theme.PALETTES[mode])
        assert re.search(r"QPushButton#CardToggle \{[^}]*text-align: left", sheet)
    assert not card.is_expanded() and card.toggle.text().startswith("▸")
    card.toggle.click()
    assert card.is_expanded() and card.toggle.text().startswith("▾")


@pytest.fixture(autouse=True)
def _light(qapp):
    yield
    theme.apply(qapp, "light")


def test_a_long_folder_is_cut_with_an_ellipsis_but_keeps_its_full_text(qapp):
    lab = ElidedLabel("/Users/someone/Library/CloudStorage/GoogleDrive-name@example.com/My Drive/Research/Sensors/2026", Qt.ElideMiddle)
    lab.show()                                                                   # a hidden widget gets no resize event yet
    lab.resize(160, 20)
    qapp.processEvents()
    assert "…" in lab.elided_text() and len(lab.elided_text()) < len(lab.full_text()) and lab.full_text().endswith("2026")
    assert lab.text() == lab.full_text()                                         # text() is always the whole string
    lab.setText("short")
    assert lab.text() == "short" and lab.elided_text() == "short"
    lab.setText("x" * 300)
    qapp.processEvents()
    assert lab.text() == "x" * 300 and "…" in lab.elided_text()
    lab.resize(900, 20)
    lab.setText("/a/b/c")
    qapp.processEvents()
    assert lab.elided_text() == lab.full_text()
