"""File ▸ Open recent: kept up to date, limited by the setting, clearable.
"""

from pathlib import Path

import pytest

from echem_analyzer import theme
from echem_analyzer.prefs import prefs
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


def entries(win):
    return [a for a in win.recent_menu.actions() if a.data()]


def make_projects(tmp_path, n=4):
    paths = []
    for i in range(n):
        f = tmp_path / f"project {i}.echemproj"
        f.write_text("x")
        paths.append(str(f))
    return paths


def test_the_file_menu_lists_the_recent_projects_newest_first_with_their_folder_on_hover(qapp, win, tmp_path):
    paths = make_projects(tmp_path, 3)
    win.session.qs.setValue("recentProjects", paths)
    win._populate_recent_menu()
    got = entries(win)
    assert [a.text() for a in got] == [Path(p).name for p in paths]
    assert [a.toolTip() for a in got] == paths and [a.statusTip() for a in got] == paths
    labels = [a.text() for a in win.recent_menu.actions()]
    assert labels[-1] == "Clear menu" and labels[-2] == ""                       # a separator, then Clear menu


def test_the_menu_is_filled_before_it_is_ever_opened_and_after_a_save(qapp, win, tmp_path):
    """A native menu bar (macOS) may not ask before showing, so the entries are kept up to date instead."""
    assert [a.text() for a in win.recent_menu.actions()] == ["No recent projects"] and not win.recent_menu.actions()[0].isEnabled()
    paths = make_projects(tmp_path, 1)
    win.session.qs.setValue("recentProjects", paths)
    win.session.project_changed.emit()
    assert [a.toolTip() for a in entries(win)] == paths


def test_the_settings_limit_applies_and_a_vanished_project_is_dropped(qapp, win, tmp_path):
    paths = make_projects(tmp_path, 5)
    win.session.qs.setValue("recentProjects", paths)
    prefs.set("recent_limit", 2)
    win._populate_recent_menu()
    assert len(entries(win)) == 2
    prefs.set("recent_limit", 12)
    Path(paths[0]).unlink()
    win._populate_recent_menu()
    assert [a.toolTip() for a in entries(win)] == paths[1:]


def test_choosing_an_entry_opens_that_project(qapp, win, tmp_path, monkeypatch):
    paths = make_projects(tmp_path, 2)
    win.session.qs.setValue("recentProjects", paths)
    win._populate_recent_menu()
    opened = []
    monkeypatch.setattr(win, "open_project_from", lambda p: opened.append(p))
    entries(win)[1].trigger()
    assert opened == [paths[1]]


def test_clear_menu_forgets_the_list_and_says_so(qapp, win, tmp_path):
    win.session.qs.setValue("recentProjects", make_projects(tmp_path, 3))
    win._populate_recent_menu()
    win.recent_menu.actions()[-1].trigger()
    assert win.session.recent_projects() == [] and [a.text() for a in win.recent_menu.actions()] == ["No recent projects"]
    assert not win.recent_menu.actions()[0].isEnabled()


def test_repopulating_does_not_leave_old_actions_behind(qapp, win, tmp_path):
    from shiboken6 import isValid
    win.session.qs.setValue("recentProjects", make_projects(tmp_path, 3))
    win._populate_recent_menu()
    first = list(win.recent_menu.actions())
    win._populate_recent_menu()
    qapp.sendPostedEvents(None, __import__("PySide6.QtCore", fromlist=["QEvent"]).QEvent.DeferredDelete)
    assert not any(isValid(a) for a in first)                                    # freed, not accumulated on the window
