"""The start page: one quiet, centred column, its footer, and the update notice under the version.

The page is tested on its own where a click would otherwise open a real (modal) dialog in the window.
"""

import pytest
from PySide6.QtWidgets import QLabel, QPushButton

from echem_analyzer import __version__, about, theme
from echem_analyzer.gui import welcome
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


@pytest.fixture()
def page(qapp):
    """A start page on its own. Inside a MainWindow its buttons are wired to real (modal) dialogs, which would wait forever here."""
    wp = welcome.WelcomePage()
    wp.resize(1500, 900)
    wp.show()
    qapp.processEvents()
    return wp


def test_the_start_page_is_one_quiet_column(page):
    wp = page
    assert not wp.logo.pixmap().isNull() and wp.version_badge.text() == f"v{__version__}"
    assert wp.btn_project.property("primary") is True and not wp.btn_files.property("primary")
    assert wp.btn_project.isVisible() and wp.btn_files.isVisible() and wp.btn_folder.isVisible()
    tiles = [b for b in wp.findChildren(QPushButton) if b.objectName() == "Tile"]
    assert len(tiles) == 6 and all(t.chip.pixmap() and not t.chip.pixmap().isNull() for t in tiles)
    for t, (full, sub, _topic, _icon, short) in zip(tiles, welcome.TILES):
        assert t.name.text() == short and full in t.toolTip() and sub in t.toolTip()      # short name on the tile, the rest on hover
    assert wp.drop_hint.isVisible() and set(wp.help_links) == {"Guide", "Keyboard shortcuts", "Settings", "About and updates"}


def test_recent_projects_are_not_on_the_start_page(page):
    """They are in File ▸ Open recent; the page stays sparse."""
    assert not hasattr(page, "recent_box") and not hasattr(page, "recent_card") and not hasattr(page, "set_recents")
    assert not any(l.objectName() in ("RecentName", "RecentPath", "RecentAge") for l in page.findChildren(QLabel))


@pytest.mark.parametrize("size", [(1920, 1080), (1500, 900), (1100, 760)])
def test_the_column_is_centred_and_no_wider_than_it_needs_to_be(qapp, win, size):
    win.resize(*size)
    for _ in range(4):
        qapp.processEvents()
    wp = win.welcome
    view = wp.scroll.viewport()                                                  # the visible area (narrower by the scroll bar, if any)
    col = wp.column.mapTo(view, wp.column.rect().topLeft())
    assert wp.column.width() <= welcome.COLUMN_WIDTH
    left, right = col.x(), view.width() - (col.x() + wp.column.width())
    assert abs(left - right) <= 2, (size, left, right)                           # centred horizontally
    above, below = col.y(), view.height() - (col.y() + wp.column.height())
    if above > 60 and below > 60:                                                # with real room to spare it floats near the middle, a little high
        assert 0.5 <= above / below <= 1.1, (size, above, below)


def test_the_footer_stays_in_view_and_is_just_the_signature_and_links(win):
    wp = win.welcome
    parent = wp.strip.parentWidget()
    while parent is not None and parent is not wp.scroll:
        parent = parent.parentWidget()
    assert parent is None                                                        # outside the scroll area: it never scrolls away
    assert wp.strip.isVisible() and wp.strip.signature.plain_text() == "Made with ♥ by Wanghley Soares Martins"
    buttons_in_strip = [b for b in wp.strip.findChildren(QPushButton)]
    assert len(buttons_in_strip) == len(about.SOCIALS) and all(b.objectName() == "IconLink" for b in buttons_in_strip)
    assert not any(b.objectName() == "Coffee" for b in wp.findChildren(QPushButton))      # the toolbar button is not repeated


def test_the_page_never_forces_the_window_wider_than_the_work_area_needs(win):
    assert win.welcome.minimumSizeHint().width() < 700                           # the work tabs set the window minimum, not this page


def test_the_page_scrolls_instead_of_clipping_in_a_short_window(qapp, win):
    win.resize(1500, 560)
    for _ in range(4):
        qapp.processEvents()
    bar = win.welcome.scroll.verticalScrollBar()
    assert bar.maximum() > 0                                                     # content taller than the window: it can be scrolled to
    bar.setValue(bar.maximum())
    qapp.processEvents()
    assert win.welcome.help_links["Guide"].isVisible()


def test_action_rows_show_the_platform_shortcuts_and_emit_their_signals(page):
    wp = page
    got = []
    wp.open_project.connect(lambda: got.append("project"))
    wp.add_files.connect(lambda: got.append("files"))
    wp.add_folder.connect(lambda: got.append("folder"))
    for b in (wp.btn_project, wp.btn_files, wp.btn_folder):
        assert b.keycap is not None and b.keycap.text()
        b.click()
    assert got == ["project", "files", "folder"]
    assert wp.btn_project.keycap.text() == welcome._native("Ctrl+O")


def test_the_help_links_open_the_guide_settings_and_about(page):
    wp = page
    got = []
    wp.open_help.connect(lambda t: got.append(("help", t)))
    wp.open_settings.connect(lambda: got.append("settings"))
    wp.open_about.connect(lambda: got.append("about"))
    for name in ("Guide", "Keyboard shortcuts", "Settings", "About and updates"):
        wp.help_links[name].click()
    assert got == [("help", "start"), ("help", "shortcuts"), "settings", "about"]


def test_the_update_notice_appears_under_the_version_and_opens_the_about_window(page):
    wp = page
    assert not wp.update_pill.isVisibleTo(wp)
    wp.set_update("Update available · v0.6.0")
    assert wp.update_pill.isVisibleTo(wp) and "v0.6.0" in wp.update_pill.text()
    got = []
    wp.open_about.connect(lambda: got.append(1))
    wp.update_pill.click()
    assert got == [1]
    wp.set_update(None)
    assert not wp.update_pill.isVisibleTo(wp)


def test_start_page_tiles_have_icons_that_follow_the_theme(win, qapp):
    tiles = [b for b in win.welcome.findChildren(QPushButton) if b.objectName() == "Tile"]
    assert len(tiles) == 6
    for t in tiles:
        assert t.chip.pixmap() is not None and not t.chip.pixmap().isNull()
    theme.apply(qapp, "dark")
    assert all(not t.chip.pixmap().isNull() for t in tiles)
