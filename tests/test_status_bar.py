"""The counts in the status bar.
"""

import pytest

from echem_analyzer import theme
from helpers import make_window


@pytest.fixture()
def win(qapp, tmp_path):
    w = make_window(tmp_path / "ws")
    w.resize(1200, 800)
    w.show()
    qapp.processEvents()
    return w


@pytest.fixture(autouse=True)
def _light(qapp):
    yield
    theme.apply(qapp, "light")


def test_the_status_bar_counts_use_the_right_plural(win):
    text = win.count_label.text()
    assert "0 files" in text and "0 measurements" in text
    assert "1 files" not in text
