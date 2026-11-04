"""Resizable panels: every side panel and split can be dragged, shows a grip, and is remembered."""
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QScrollArea

from echem_analyzer.gui.ui_kit import Splitter
from helpers import make_window
from test_gui_platform import lab                                                     # noqa: F401  (fixture: synthetic files)


def main_split(panel) -> Splitter:
    return next(s for s in panel.findChildren(Splitter) if s.orientation() == Qt.Horizontal and s.count() == 2
                and isinstance(s.widget(1), QScrollArea))


def show_study(win, qapp):
    win.resize(1560, 960)
    win.show()
    win.tabs.setCurrentWidget(win.study_tab)
    win.study_tab.tabs.setCurrentWidget(win.study_tab.calibration)
    qapp.processEvents()
    return main_split(win.study_tab.calibration)


def close_clean(win):
    """Close the window the way the user would after saving: loaded data makes the session dirty, and a dirty one asks first."""
    win.session.dirty = False
    win.close()


def drag(handle, dx):
    c = QPoint(handle.width() // 2, handle.height() // 2)
    QTest.mousePress(handle, Qt.LeftButton, Qt.NoModifier, c)
    QTest.mouseMove(handle, c + QPoint(dx, 0))
    QTest.mouseRelease(handle, Qt.LeftButton, Qt.NoModifier, c + QPoint(dx, 0))


def test_no_panel_is_locked_to_a_fixed_width(qapp, tmp_path):
    win = make_window(tmp_path / "w")
    locked = [w for w in win.findChildren(QScrollArea) if 0 < w.minimumWidth() == w.maximumWidth()]
    assert not locked
    side = [s.widget(1) for s in win.findChildren(Splitter) if s.orientation() == Qt.Horizontal and s.count() == 2 and isinstance(s.widget(1), QScrollArea)]
    assert len(side) >= 6 and all(w.minimumWidth() == 280 and w.maximumWidth() > 5000 for w in side)        # CA, CV, Analysis, Electrode x2, Study...
    assert len(win.findChildren(Splitter)) >= 20 and all(s.handleWidth() >= 10 for s in win.findChildren(Splitter))


def test_dragging_resizes_and_is_remembered_and_double_click_resets(qapp, lab, tmp_path):
    folder = tmp_path / "w"
    win = make_window(folder)
    win.load_paths([lab])
    sp = show_study(win, qapp)
    handle = sp.handle(1)
    assert sp.sizes()[1] == pytest.approx(372, abs=6) and "double-click" in handle.toolTip()
    total = sum(sp.sizes())
    drag(handle, -150)                                                                # make the right panel 150 px wider
    wide = sp.sizes()[1]
    assert wide == pytest.approx(372 + 150, abs=12) and sum(sp.sizes()) == pytest.approx(total, abs=2)
    drag(handle, 400)                                                                 # ... and far narrower: stops at its minimum
    assert sp.sizes()[1] == 280
    drag(handle, -300)
    chosen = sp.sizes()[1]
    assert sp.saved_sizes() == sp.sizes()

    close_clean(win)                                                                  # a new session with the same settings file
    win2 = make_window(folder)
    win2.load_paths([lab])
    sp2 = show_study(win2, qapp)
    assert sp2.sizes()[1] == pytest.approx(chosen, abs=8)

    QTest.mouseDClick(sp2.handle(1), Qt.LeftButton, Qt.NoModifier, QPoint(5, 20))
    qapp.processEvents()
    assert sp2.sizes()[1] == pytest.approx(372, abs=8) and sp2.saved_sizes() is None


def test_reset_panel_sizes_action_and_dock_width(qapp, lab, tmp_path):
    folder = tmp_path / "w"
    win = make_window(folder)
    win.load_paths([lab])
    sp = show_study(win, qapp)
    drag(sp.handle(1), -200)
    win.resizeDocks([win.files_dock], [420], Qt.Horizontal)
    qapp.processEvents()
    assert win.files_dock.width() == pytest.approx(420, abs=10)
    close_clean(win)
    win2 = make_window(folder)
    win2.load_paths([lab])
    sp2 = show_study(win2, qapp)
    assert win2.files_dock.width() == pytest.approx(420, abs=15) and sp2.sizes()[1] == pytest.approx(572, abs=15)
    win2.reset_layout()
    qapp.processEvents()
    assert sp2.sizes()[1] == pytest.approx(372, abs=8) and win2.files_dock.width() == pytest.approx(win2.DOCK_DEFAULT, abs=10)
    assert win2.session.qs.value("layout/filesDockWidth") is None


def test_damaged_saved_layout_is_ignored(qapp, tmp_path):
    win = make_window(tmp_path / "w")
    sp = main_split(win.study_tab.calibration)
    for bad in ("garbage", [0, 100], [5], None, ["a", "b"]):
        win.session.qs.setValue(sp._key, bad)
        assert sp.saved_sizes() is None
    win.session.qs.setValue(sp._key, [600, 300])
    assert sp.saved_sizes() == [600, 300]


def test_every_tab_still_draws_with_the_grips(qapp, lab, tmp_path):
    win = make_window(tmp_path / "w")
    win.load_paths([lab])
    win.resize(1400, 900)
    win.show()
    for i in range(win.tabs.count()):
        win.tabs.setCurrentIndex(i)
        qapp.processEvents()
        assert not win.grab().isNull()
