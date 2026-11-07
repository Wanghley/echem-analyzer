"""Small reusable widgets: the folded card header and the elided label.
"""

import re

import pytest
from PySide6.QtWidgets import QPushButton

from echem_analyzer import theme
from echem_analyzer.gui.ui_kit import CollapsibleCard


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
