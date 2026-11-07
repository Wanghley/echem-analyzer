"""The left panel: the open project's name, where it is saved, and its files.
"""

import pytest

from echem_analyzer import theme
from echem_analyzer.gui import main_window as mw
from helpers import make_window


@pytest.fixture(autouse=True)
def _light(qapp):
    yield
    theme.apply(qapp, "light")


@pytest.fixture()
def win(qapp, tmp_path):
    w = make_window(tmp_path / "ws")
    w.resize(1500, 900)
    w.show()
    qapp.processEvents()
    return w


def open_demo_project(win, tmp_path, name="B2 GY data"):
    from echem_analyzer.selftest import _synthetic_project
    win.session.add_datafile(_synthetic_project().files[0])
    win.session.project_path = tmp_path / f"{name}.echemproj"
    win._update_title()


def test_the_left_panel_is_the_project_with_its_name_its_place_and_its_files(qapp, win, tmp_path):
    assert win.files_dock.windowTitle() == "PROJECT" and not win.files_dock.isVisible()      # nothing open: no panel
    open_demo_project(win, tmp_path)
    qapp.processEvents()
    assert win.files_dock.isVisible() and win.file_tree.topLevelItemCount() == 1
    assert win.name_label.text().startswith("B2 GY data") and win.sub_label.text() == str(tmp_path / "B2 GY data.echemproj")
    assert win.name_label.window() is win and win.files_dock.isAncestorOf(win.name_label)     # in the panel, not in the toolbar
    assert not win.toolbar.isAncestorOf(win.name_label) and not win.toolbar.isAncestorOf(win.sub_label)
    assert not win.project_chip.pixmap().isNull()


def test_the_panel_shows_the_unsaved_marker_and_an_untitled_project(qapp, win, tmp_path):
    assert win.name_label.text() == "Untitled" and win.sub_label.text() == "Unsaved project"
    open_demo_project(win, tmp_path)
    win.session.touch()
    assert win.name_label.text().endswith("●")
    win.session.dirty = False
    win._update_title()
    assert not win.name_label.text().endswith("●")


def test_a_long_project_path_is_cut_in_the_panel_but_kept_whole_in_the_tooltip(qapp, win, tmp_path):
    deep = tmp_path / ("a-very-long-folder-name-" * 6) / "project.echemproj"
    open_demo_project(win, tmp_path)
    win.session.project_path = deep
    win._update_title()
    qapp.processEvents()
    assert win.sub_label.text() == str(deep) and win.sub_label.toolTip() == str(deep)
    assert "…" in win.sub_label.elided_text() and win.sub_label.width() <= win.files_dock.width()


def test_the_project_panel_stays_while_the_start_page_shows(qapp, win, tmp_path):
    open_demo_project(win, tmp_path)
    win.show_work()
    win.show_home()
    qapp.processEvents()
    assert win.stack.currentIndex() == mw.PAGE_WELCOME and win.files_dock.isVisible()
    assert win.welcome.current_card.isVisibleTo(win.welcome)                     # and the way back is on the page
