"""The author information, the support widgets (the coffee button, the support card, the signature) and how they sit in the window.

None of these tests needs the real data folder, and none opens a browser: ``support.open_link`` is replaced by a recorder.
"""

import re

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPushButton

import echem_analyzer
from echem_analyzer import about, prefs as prefs_mod, theme
from echem_analyzer.gui import main_window as mw, support
from echem_analyzer.prefs import prefs
from helpers import make_window


@pytest.fixture()
def opened(monkeypatch):
    """Record every link the app tries to open instead of launching a browser."""
    urls: list[str] = []
    monkeypatch.setattr(support, "open_link", lambda url: urls.append(url) or True)
    return urls


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


def test_author_and_links_are_the_ones_the_owner_gave():
    assert about.AUTHOR == "Wanghley Soares Martins"
    assert about.COFFEE.url == "https://buymeacoffee.com/wanghley"
    assert about.WEBSITE.url == "https://wanghley.com"
    assert about.GITHUB.url == "https://github.com/Wanghley"
    assert about.LINKEDIN.url == "https://www.linkedin.com/in/wanghley/"
    assert about.INSTAGRAM.url == "https://www.instagram.com/wanghley/"


def test_every_link_is_https_unique_and_has_an_icon():
    keys = [l.key for l in about.ALL_LINKS]
    assert len(keys) == len(set(keys))
    for link in about.ALL_LINKS:
        assert re.fullmatch(r"https://[\w.-]+(/[\w./-]*)?", link.url), link.url
        assert link.icon in theme._SVG, f"no icon {link.icon!r} for {link.key}"
    assert about.COFFEE not in about.SOCIALS                      # the coffee link is the call to action, not a profile


def test_the_source_has_no_other_coffee_or_profile_url():
    """A link typed into some other module would drift from ``about``: every URL in the GUI package must come from it."""
    import pathlib
    gui = pathlib.Path(echem_analyzer.__file__).parent / "gui"
    known = {l.url for l in about.ALL_LINKS}
    for path in gui.glob("*.py"):
        for url in re.findall(r"https?://[^\s\"')]+", path.read_text(encoding="utf-8")):
            assert url in known, f"{path.name}: {url}"


def test_the_package_metadata_names_the_author():
    import pathlib
    root = pathlib.Path(echem_analyzer.__file__).parents[1]
    assert about.AUTHOR in (root / "pyproject.toml").read_text(encoding="utf-8")
    assert about.AUTHOR in (root / "packaging" / "build.py").read_text(encoding="utf-8")


def test_support_popup_preference_defaults_on_and_only_accepts_booleans():
    assert prefs.get("support_popup") is True
    assert prefs_mod.clean("support_popup", "no") is True         # junk falls back to the default
    assert prefs_mod.clean("support_popup", False) is False
    prefs.set("support_popup", False)
    assert prefs.get("support_popup") is False


def test_a_link_button_opens_its_link_and_keeps_an_icon_in_both_themes(qapp, opened):
    b = support.LinkButton(about.GITHUB)
    assert b.text().strip() == "GitHub" and b.accessibleName() == "GitHub" and about.GITHUB.url in b.toolTip()
    assert not b.icon().isNull()
    theme.apply(qapp, "dark")
    assert not b.icon().isNull()                                  # re-tinted by the theme bus, never lost
    b.click()
    assert opened == [about.GITHUB.url]


def test_the_coffee_button_can_be_a_plain_trigger(opened):
    b = support.coffee_button(compact=True, open_on_click=False)
    b.click()
    assert opened == []                                           # the toolbar one only opens the card
    support.coffee_button().click()
    assert opened == [about.COFFEE.url]


def test_the_coffee_button_is_always_in_the_toolbar_and_opens_the_card(win, opened):
    assert win.coffee_btn.isVisible() and win.coffee_btn.text().strip() == "Buy me a coffee"
    assert not win.support_card.isVisible()                       # not on its own: only the entry point schedules it
    win.coffee_btn.click()
    assert win.support_card.isVisible() and opened == []          # opening the card does not open the browser
    win.coffee_btn.click()
    assert not win.support_card.isVisible()                       # a second click closes it again


def test_the_coffee_button_stays_in_the_toolbar_on_the_start_page_without_being_repeated(win):
    assert win.stack.currentIndex() == mw.PAGE_WELCOME and win.coffee_btn.isVisible()
    assert not any(b.objectName() == "Coffee" for b in win.welcome.findChildren(QPushButton))


def test_the_card_coffee_button_opens_the_page_and_closes_the_card(win, opened):
    win.support_card.pop(animate=False)
    assert win.support_card.isVisible()
    win.support_card.coffee.click()
    assert opened == [about.COFFEE.url] and not win.support_card.isVisible()


def test_maybe_later_closes_without_changing_the_preference(win):
    win.support_card.pop(animate=False)
    later = next(b for b in win.support_card.findChildren(QPushButton) if b.text() == "Maybe later")
    later.click()
    assert not win.support_card.isVisible() and prefs.get("support_popup") is True


def test_dont_show_at_startup_turns_the_preference_off_and_tells_the_user(win):
    win.support_card.pop(animate=False)
    never = next(b for b in win.support_card.findChildren(QPushButton) if b.objectName() == "QuietLink")
    never.click()
    assert prefs.get("support_popup") is False and not win.support_card.isVisible()
    assert "Settings" in win.statusBar().currentMessage()
    assert win.coffee_btn.isVisible()                             # the button never goes away


def test_the_card_sits_inside_the_window_and_follows_a_resize(win, qapp):
    win.support_card.pop(animate=False)
    for size in ((1200, 800), (900, 620)):
        win.resize(*size)
        qapp.processEvents()
        r = win.support_card.geometry()
        assert win.rect().contains(r), (size, r)
        assert r.right() > win.width() - 60 and r.bottom() > win.height() - 90      # still in the bottom-right corner


def test_the_card_does_not_cover_the_status_bar(win, qapp):
    win.support_card.pop(animate=False)
    qapp.processEvents()
    assert win.support_card.geometry().bottom() < win.statusBar().geometry().top()


def test_the_entry_point_schedules_the_card_only_when_the_preference_is_on(win, monkeypatch):
    calls = []
    monkeypatch.setattr(QTimer, "singleShot", staticmethod(lambda ms, fn: calls.append((ms, fn))))
    win.schedule_support_popup(500)
    assert len(calls) == 1 and calls[0][0] == 500
    prefs.set("support_popup", False)
    win.schedule_support_popup(500)
    assert len(calls) == 1                                        # switched off: nothing scheduled


def test_the_scheduled_popup_shows_once_and_respects_a_change_made_meanwhile(win):
    win._show_startup_card()
    assert win.support_card.isVisible()
    win.support_card.close_card()
    prefs.set("support_popup", False)                             # turned off between scheduling and firing
    win._show_startup_card()
    assert not win.support_card.isVisible()


def test_building_a_window_never_shows_the_card(qapp, tmp_path):
    """Tests, --selftest and anything else that builds a MainWindow must not get a popup: only __main__ schedules it."""
    w = make_window(tmp_path / "quiet")
    w.show()
    for _ in range(5):
        qapp.processEvents()
    assert not w.support_card.isVisible()


def test_the_settings_checkbox_mirrors_and_writes_the_preference(win):
    win.show_settings()
    box = win._settings.support_popup
    assert box.isChecked()
    box.setChecked(False)
    assert prefs.get("support_popup") is False
    prefs.set("support_popup", True)
    win._settings._load()
    assert box.isChecked()


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_the_style_sheet_builds_in_both_themes_with_the_brand_yellow(qapp, mode):
    p = theme.apply(qapp, mode)
    sheet = qapp.styleSheet()
    assert about.COFFEE_BG in sheet and "QPushButton#Coffee" in sheet and "QComboBox" in sheet
    assert p.name == mode
    assert sheet.count("{") == sheet.count("}")                   # no brace lost to f-string escaping


def test_the_arrow_images_exist_where_the_style_sheet_points(qapp):
    import os
    theme.apply(qapp, "light")
    for path in re.findall(r'url\("([^"]+\.png)"\)', qapp.styleSheet()):
        assert os.path.exists(path), path


def test_the_toolbar_separators_have_no_border(qapp):
    theme.apply(qapp, "light")
    assert re.search(r"QFrame#ToolSep \{[^}]*border: none", qapp.styleSheet())
