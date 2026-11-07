"""Updates: the update manager (state, worker thread), the Updates card and version information in the About window, and the update
flow in the main window.

No network and no real installer: the update manager gets fake ``fetch`` / ``prepare`` / ``launch`` functions, and every link goes to a
recorder. Jobs run on the manager's real worker thread, so these tests also prove the GUI thread is never blocked or touched.
"""

import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QThread, QUrl
from PySide6.QtWidgets import QApplication, QMessageBox

from echem_analyzer import __version__, about, theme, updater
from echem_analyzer.gui import about_dialog, support, main_window as mw
from echem_analyzer.gui.about_dialog import AboutDialog, details_text, version_rows
from echem_analyzer.gui.ui_kit import ElidedLabel
from echem_analyzer.gui.update_manager import State, UpdateManager
from echem_analyzer.gui.update_panel import UpdatePanel
from echem_analyzer.prefs import prefs
from helpers import make_window


BASE = "https://github.com/Wanghley/echem-analyzer/releases/download"


def release(version="0.6.0", notes="## What's new\n- better things"):
    names = [updater.asset_name(k, version) for k in ("macos-arm64", "macos-x86_64", "windows-x64", "linux-amd64")] + [updater.SUMS_NAME]
    return updater.parse_release({
        "tag_name": f"v{version}", "name": "x", "body": notes, "draft": False, "prerelease": False,
        "html_url": f"https://github.com/Wanghley/echem-analyzer/releases/tag/v{version}", "published_at": "2026-11-20T10:00:00Z",
        "assets": [{"name": n, "size": 5000, "browser_download_url": f"{BASE}/v{version}/{n}"} for n in names]})


def wait_until(qapp, cond, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.004)
    return False


def idle(qapp, mgr):
    """Wait until the manager's worker threads have all finished and been cleaned up."""
    assert wait_until(qapp, lambda: not mgr._running), "a worker thread did not finish"


class Fakes:
    """Controllable stand-ins for the network, the download and the launch."""

    def __init__(self, version="0.6.0"):
        self.release = release(version)
        self.fetch_calls = 0
        self.fetch_threads: list[int] = []
        self.fetch_error: Exception | None = None
        self.prepare_error: Exception | None = None
        self.prepare_wait: threading.Event | None = None
        self.launched: list = []
        self.plan_error: Exception | None = None

    def fetch(self):
        self.fetch_calls += 1
        self.fetch_threads.append(threading.get_ident())
        if self.fetch_error:
            raise self.fetch_error
        return self.release

    def prepare(self, rel, asset, progress, cancelled, stage):
        stage("download")
        for i in range(1, 6):
            if cancelled():
                raise updater.Cancelled()
            if self.prepare_wait is not None and i == 3:
                self.prepare_wait.wait(5)
            progress(i * 1000, 5000)
            time.sleep(0.01)
        if self.prepare_error:
            raise self.prepare_error
        stage("verify")
        return updater.Prepared(rel, asset, Path("/tmp/fake-installer"), "0" * 64)

    def build_plan(self, prepared):
        if self.plan_error:
            raise self.plan_error
        return "plan"

    def launch(self, plan):
        self.launched.append(plan)


@pytest.fixture()
def fakes():
    return Fakes()


@pytest.fixture()
def mgr(qapp, fakes):
    clock = [1_800_000_000.0]
    m = UpdateManager(fetch=fakes.fetch, prepare=fakes.prepare, build_plan=fakes.build_plan, launch=fakes.launch, now=lambda: clock[0])
    m.clock = clock
    m.kind, m.key = "installed", "macos-arm64"
    yield m
    m.shutdown()
    qapp.processEvents()


@pytest.fixture()
def opened(monkeypatch):
    urls: list[str] = []
    monkeypatch.setattr(support, "open_link", lambda url: urls.append(url) or True)
    monkeypatch.setattr("echem_analyzer.gui.update_panel.open_link", lambda url: urls.append(url) or True)
    return urls


@pytest.fixture(autouse=True)
def _light(qapp):
    yield
    theme.apply(qapp, "light")


def check_and_wait(qapp, mgr, manual=True):
    mgr.check(manual=manual)
    idle(qapp, mgr)


def get_ready(qapp, mgr):
    check_and_wait(qapp, mgr)
    mgr.download()
    idle(qapp, mgr)
    assert mgr.state is State.READY


def test_a_check_runs_off_the_gui_thread_and_finds_the_newer_release(qapp, mgr, fakes):
    seen = []
    mgr.state_changed.connect(lambda: seen.append(mgr.state))
    check_and_wait(qapp, mgr)
    assert fakes.fetch_threads and all(t != threading.get_ident() for t in fakes.fetch_threads)      # never on the GUI thread
    assert seen[0] is State.CHECKING and mgr.state is State.AVAILABLE
    assert mgr.release.text == "0.6.0" and mgr.asset.name == "Echem-Analyzer-0.6.0-macos-arm64.dmg" and mgr.can_auto_install
    assert prefs.get("update_seen") == "0.6.0" and prefs.get("last_update_check") == mgr.clock[0]
    assert mgr.badge_text() == "Update available · v0.6.0"


def test_up_to_date_clears_the_remembered_update(qapp, mgr, fakes):
    prefs.set("update_seen", "0.6.0")
    fakes.release = release(__version__)
    check_and_wait(qapp, mgr)
    assert mgr.state is State.UP_TO_DATE and prefs.get("update_seen") == "" and mgr.badge_text() == ""
    fakes.release = release("0.0.1")                                            # a build ahead of the newest release is fine too
    check_and_wait(qapp, mgr)
    assert mgr.state is State.UP_TO_DATE


def test_a_manual_check_reports_a_failure_but_an_automatic_one_stays_quiet(qapp, mgr, fakes):
    fakes.fetch_error = urllib_error()
    check_and_wait(qapp, mgr, manual=True)
    assert mgr.state is State.ERROR and "Can't reach GitHub" in mgr.error
    check_and_wait(qapp, mgr, manual=False)
    assert mgr.state is State.IDLE and mgr.error == ""


def urllib_error():
    import urllib.error
    return urllib.error.URLError("no network")


def test_the_startup_check_obeys_the_preference_and_the_daily_limit(qapp, mgr, fakes):
    prefs.set("update_check", False)
    mgr.startup_check()
    idle(qapp, mgr)
    assert fakes.fetch_calls == 0                                                # switched off: the network is never touched
    prefs.set("update_check", True)
    fakes.release = release(__version__)
    mgr.startup_check()
    idle(qapp, mgr)
    assert fakes.fetch_calls == 1
    mgr.clock[0] += 3600
    mgr.startup_check()
    idle(qapp, mgr)
    assert fakes.fetch_calls == 1                                                # an hour later: too soon
    mgr.clock[0] += 25 * 3600
    mgr.startup_check()
    idle(qapp, mgr)
    assert fakes.fetch_calls == 2


def test_a_known_pending_update_is_rechecked_at_startup_even_within_a_day(qapp, mgr, fakes):
    prefs.set("last_update_check", mgr.clock[0] - 60)
    prefs.set("update_seen", "0.6.0")
    assert mgr.hint_version() == "0.6.0" and mgr.badge_text() == "Update available · v0.6.0"       # shown before any network
    mgr.startup_check()
    idle(qapp, mgr)
    assert fakes.fetch_calls == 1 and mgr.state is State.AVAILABLE


def test_a_stale_hint_for_a_version_already_installed_is_ignored(mgr):
    prefs.set("update_seen", __version__)
    assert mgr.hint_version() is None and mgr.badge_text() == ""
    prefs.set("update_seen", "garbage")
    assert mgr.hint_version() is None


def test_a_second_check_is_ignored_while_one_is_running(qapp, mgr, fakes):
    gate = threading.Event()
    original = fakes.fetch
    fakes.fetch = lambda: (gate.wait(5), original())[1]
    mgr._fetch = fakes.fetch
    mgr.check()
    assert mgr.state is State.CHECKING and mgr.busy
    mgr.check()
    gate.set()
    idle(qapp, mgr)
    assert fakes.fetch_calls == 1


def test_download_goes_through_verify_to_ready_with_progress(qapp, mgr, fakes):
    check_and_wait(qapp, mgr)
    states, progress = [], []
    mgr.state_changed.connect(lambda: states.append(mgr.state))
    mgr.progress_changed.connect(lambda d, t: progress.append((d, t)))
    mgr.download()
    idle(qapp, mgr)
    assert states[0] is State.DOWNLOADING and State.VERIFYING in states and states[-1] is State.READY
    assert progress[-1] == (5000, 5000) and [d for d, _ in progress] == sorted(d for d, _ in progress)
    assert mgr.prepared is not None and mgr.badge_text() == "Update ready · restart to install"


def test_cancelling_a_download_returns_to_available(qapp, mgr, fakes):
    check_and_wait(qapp, mgr)
    fakes.prepare_wait = threading.Event()
    mgr.download()
    assert wait_until(qapp, lambda: mgr.state is State.DOWNLOADING)
    mgr.cancel()
    fakes.prepare_wait.set()
    idle(qapp, mgr)
    assert mgr.state is State.AVAILABLE and mgr.prepared is None


def test_a_failed_download_shows_the_reason_and_can_be_retried(qapp, mgr, fakes):
    check_and_wait(qapp, mgr)
    fakes.prepare_error = updater.UpdateError("The downloaded file does not match its published checksum, so it was deleted. Try again.")
    mgr.download()
    idle(qapp, mgr)
    assert mgr.state is State.ERROR and "checksum" in mgr.error
    fakes.prepare_error = None
    mgr.retry()
    idle(qapp, mgr)
    assert mgr.state is State.READY


def test_download_does_nothing_unless_an_update_is_waiting(qapp, mgr, fakes):
    mgr.download()
    assert mgr.state is State.IDLE and fakes.fetch_calls == 0
    mgr.kind = "portable"
    check_and_wait(qapp, mgr)
    assert mgr.state is State.AVAILABLE and not mgr.can_auto_install
    mgr.download()
    assert mgr.state is State.AVAILABLE                                          # no installer for this copy: nothing is downloaded


@pytest.mark.parametrize("kind,key,words", [("source", "macos-arm64", "from source"), ("portable", "windows-x64", "portable"),
                                            ("installed", None, "no installer for this kind"), ("installed", "linux-arm64", "no installer for this system")])
def test_a_copy_that_cannot_update_itself_says_why(qapp, mgr, kind, key, words):
    mgr.kind, mgr.key = kind, key
    check_and_wait(qapp, mgr)
    assert not mgr.can_auto_install and words in mgr.manual_install_note()


def test_installing_launches_the_helper_once_and_only_when_ready(qapp, mgr, fakes):
    assert "no downloaded update" in mgr.launch_installer() and fakes.launched == []
    get_ready(qapp, mgr)
    assert mgr.launch_installer() == "" and fakes.launched == ["plan"] and mgr.state is State.INSTALLING
    assert mgr.badge_text() == ""                                                # the app is about to close: nothing more to announce


def test_a_plan_that_cannot_be_built_leaves_the_user_with_a_message_not_a_crash(qapp, mgr, fakes):
    get_ready(qapp, mgr)
    fakes.plan_error = updater.UpdateError("The folder /Applications is not writable for you.")
    msg = mgr.launch_installer()
    assert "not writable" in msg and mgr.state is State.ERROR and fakes.launched == []


def test_install_requested_is_only_emitted_when_ready(qapp, mgr):
    got = []
    mgr.install_requested.connect(lambda: got.append(1))
    mgr.request_install()
    assert got == []
    get_ready(qapp, mgr)
    mgr.request_install()
    assert got == [1]


def test_shutdown_waits_for_a_running_job_and_leaves_no_thread(qapp, mgr, fakes):
    slow = fakes.fetch
    fakes.fetch = lambda: (time.sleep(0.3), slow())[1]
    mgr._fetch = fakes.fetch
    mgr.check()
    assert mgr._running and isinstance(mgr._running[0][0], QThread)
    mgr.shutdown()
    assert mgr._running == []
    for _ in range(5):
        qapp.processEvents()                                                     # late signals from the finished job must be harmless


@pytest.fixture()
def panel(qapp, mgr):
    p = UpdatePanel(mgr)
    p.show()
    qapp.processEvents()
    return p


def put(mgr, state, rel=None, auto=True):
    mgr.release = rel
    mgr.asset = updater.choose_asset(rel, "macos-arm64", "installed") if rel and auto else None
    mgr.error = "Can't reach GitHub. Check your internet connection and try again."
    mgr._set(state)


@pytest.mark.parametrize("state,title,main,enabled,second", [
    (State.IDLE, "Check for updates", "Check for updates", True, False),
    (State.CHECKING, "Checking for updates…", "Checking…", False, False),
    (State.UP_TO_DATE, "You are up to date", "Check again", True, False),
    (State.AVAILABLE, "Version 0.6.0 is available", "Download and install", True, True),
    (State.DOWNLOADING, "Downloading version 0.6.0…", "Cancel", True, False),
    (State.VERIFYING, "Verifying version 0.6.0…", "Verifying…", False, False),
    (State.READY, "Version 0.6.0 is ready to install", "Install and restart", True, True),
    (State.INSTALLING, "Installing the update…", "Installing…", False, False),
    (State.ERROR, "The update did not finish", "Try again", True, True),
])
def test_the_panel_shows_the_right_words_and_buttons_for_each_state(qapp, mgr, panel, state, title, main, enabled, second):
    put(mgr, state, release() if state not in (State.IDLE, State.CHECKING) else None)
    qapp.processEvents()
    assert panel.title.text() == title
    assert panel.main.text().strip() == main and panel.main.isEnabled() is enabled
    assert panel.second.isVisibleTo(panel) is second
    assert not panel.chip.pixmap().isNull()


def test_the_buttons_do_the_one_next_step(qapp, mgr, panel, opened, monkeypatch):
    calls = []
    for name in ("check", "download", "cancel", "request_install", "retry"):
        monkeypatch.setattr(mgr, name, lambda *a, _n=name, **k: calls.append(_n))
    rel = release()
    for state, expected in ((State.IDLE, "check"), (State.UP_TO_DATE, "check"), (State.AVAILABLE, "download"),
                            (State.DOWNLOADING, "cancel"), (State.READY, "request_install"), (State.ERROR, "retry")):
        calls.clear()
        put(mgr, state, rel)
        panel.main.click()
        assert calls == [expected], state
    put(mgr, State.AVAILABLE, rel)
    panel.second.click()
    assert opened == [rel.page]


def test_a_copy_that_cannot_self_update_opens_the_download_page_instead(qapp, mgr, panel, opened):
    rel = release()
    put(mgr, State.AVAILABLE, rel, auto=False)
    assert panel.main.text().strip() == "Open the download page" and "release page" in panel.detail.text()
    panel.main.click()
    assert opened == [rel.page] and mgr.state is State.AVAILABLE


def test_the_progress_bar_follows_the_download(qapp, mgr, panel):
    put(mgr, State.DOWNLOADING, release())
    mgr.done, mgr.total = 2500, 5000
    mgr.progress_changed.emit(2500, 5000)
    assert panel.bar.isVisibleTo(panel) and panel.bar.maximum() == 1000 and panel.bar.value() == 500
    assert "2.5 KB of 5.0 KB" in panel.detail.text()
    put(mgr, State.IDLE)
    assert not panel.bar.isVisibleTo(panel)


def test_release_notes_are_shown_as_text_never_as_code(qapp, mgr, panel, opened):
    hostile = ("## Notes\n<script>alert(1)</script>\n<img src=\"http://evil.example/x.png\">\n"
               "<a href=\"javascript:alert(1)\">click</a>\n\n[good](https://github.com/Wanghley/echem-analyzer)")
    put(mgr, State.AVAILABLE, release(notes=hostile))
    panel._show_notes(True)
    html = panel.notes.toHtml().lower()
    assert "<script" not in html and "<img" not in html and "href=\"javascript" not in html
    assert "alert(1)" in panel.notes.toPlainText()                               # shown as plain text
    panel._note_link(QUrl("javascript:alert(1)"))
    panel._note_link(QUrl("http://evil.example/"))
    panel._note_link(QUrl("file:///etc/passwd"))
    assert opened == []                                                          # only https links open, and only on a click
    panel._note_link(QUrl("https://github.com/Wanghley/echem-analyzer"))
    assert opened == ["https://github.com/Wanghley/echem-analyzer"]


def test_notes_are_hidden_when_there_are_none_and_follow_the_version(qapp, mgr, panel):
    put(mgr, State.AVAILABLE, release(notes="   "))
    assert not panel.notes_toggle.isVisibleTo(panel)
    put(mgr, State.AVAILABLE, release(notes="- something"))
    assert panel.notes_toggle.isVisibleTo(panel) and "0.6.0" in panel.notes_toggle.text()
    assert not panel.notes.isVisibleTo(panel)                                    # folded until asked for
    panel.notes_toggle.click()
    assert panel.notes.isVisibleTo(panel)


def test_the_automatic_check_checkbox_writes_the_preference(qapp, mgr, panel):
    assert panel.auto.isChecked() and prefs.get("update_check") is True
    panel.auto.setChecked(False)
    assert prefs.get("update_check") is False
    panel.auto.setChecked(True)
    assert prefs.get("update_check") is True


def test_the_panel_survives_a_theme_change_in_every_state(qapp, mgr, panel):
    rel = release()
    for state in State:
        put(mgr, state, rel)
        for mode in ("dark", "light"):
            theme.apply(qapp, mode)
            qapp.processEvents()
            assert not panel.chip.pixmap().isNull() and not panel.main.icon().isNull()


def test_the_about_window_reports_what_is_running(qapp, mgr):
    d = AboutDialog(None, mgr)
    d.show()
    qapp.processEvents()
    rows = dict(version_rows())
    assert rows["Version"] == __version__ and rows["Qt"] and rows["PySide6"] and rows["Python"] and rows["Installation"] and rows["Location"]
    shown = {k: v.full_text() if isinstance(v, ElidedLabel) else v.text() for k, v in d.info_values.items()}
    assert shown == rows
    assert d.updates is mgr and d.update_panel.manager is mgr                    # one shared state, not a private copy


def test_copy_details_puts_the_version_information_on_the_clipboard(qapp, mgr):
    d = AboutDialog(None, mgr)
    d.show()
    d.copy_btn.click()
    text = QApplication.clipboard().text()
    assert text == details_text() and text.startswith("Echem Analyzer\n") and f"Version: {__version__}" in text
    assert d.copy_btn.text().strip() == "Copied"
    line = about_dialog.version_line()
    assert f"Version {__version__}" in line and "Python" in line and "PySide6" in line and "Qt" in line


def test_the_about_window_has_its_own_manager_when_opened_alone(qapp):
    d = AboutDialog()
    assert isinstance(d.updates, UpdateManager)
    d.updates.shutdown()


def test_the_about_window_footer_carries_the_signature(qapp, mgr):
    d = AboutDialog(None, mgr)
    assert d.signature.plain_text() == f"Made with ♥ by {about.AUTHOR}"


@pytest.fixture()
def win(qapp, tmp_path):
    w = make_window(tmp_path / "ws")
    w.resize(1500, 900)
    w.show()
    qapp.processEvents()
    return w


def test_the_toolbar_badge_and_the_start_page_follow_the_manager(qapp, win, fakes):
    win.updates._fetch, win.updates.kind, win.updates.key = fakes.fetch, "installed", "macos-arm64"
    assert not win.update_btn.isVisibleTo(win) and not win.welcome.update_pill.isVisibleTo(win.welcome)
    check_and_wait(qapp, win.updates)
    assert win.update_btn.isVisible() and "v0.6.0" in win.update_btn.text() and not win.update_btn.icon().isNull()
    assert win.welcome.update_pill.isVisible() and "v0.6.0" in win.welcome.update_pill.text()
    prefs.set("update_seen", "")
    win.updates._set(State.UP_TO_DATE)
    qapp.processEvents()                                                         # a toolbar hides the widget on its next layout pass
    assert not win.update_btn.isVisible() and not win.welcome.update_pill.isVisible()


def test_the_badge_opens_the_about_window_with_the_windows_manager(qapp, win, fakes, monkeypatch):
    shown = []
    monkeypatch.setattr(AboutDialog, "exec", lambda self: shown.append(self.updates) or 0)
    win.updates._fetch, win.updates.kind, win.updates.key = fakes.fetch, "installed", "macos-arm64"
    check_and_wait(qapp, win.updates)
    qapp.processEvents()
    assert win.update_btn.isVisible()
    win.update_btn.click()
    assert shown == [win.updates]


def test_a_new_window_never_touches_the_network(qapp, tmp_path, fakes):
    w = make_window(tmp_path / "quiet")
    w.updates._fetch = fakes.fetch
    w.show()
    for _ in range(10):
        qapp.processEvents()
    assert fakes.fetch_calls == 0 and w.updates.state is State.IDLE and w.updates._running == []


def test_the_entry_point_schedules_the_check_after_a_delay(win, monkeypatch):
    from PySide6.QtCore import QTimer
    calls = []
    monkeypatch.setattr(QTimer, "singleShot", staticmethod(lambda ms, fn: calls.append((ms, fn))))
    win.schedule_update_check(1234)
    assert calls == [(1234, win.updates.startup_check)]


def test_help_menu_check_for_updates_starts_a_check_and_shows_about(win, monkeypatch):
    started, shown = [], []
    monkeypatch.setattr(win.updates, "check", lambda manual=True: started.append(manual))
    monkeypatch.setattr(win, "show_about", lambda: shown.append(1))
    help_menu = next(a.menu() for a in win.menuBar().actions() if a.text().replace("&", "") == "Help")
    action = next(a for a in help_menu.actions() if a.text().startswith("Check for updates"))
    action.trigger()
    assert started == [True] and shown == [1]


def test_about_opens_with_the_windows_manager_and_is_freed_afterwards(qapp, win, monkeypatch):
    seen = []
    monkeypatch.setattr(AboutDialog, "exec", lambda self: seen.append(self) or 0)
    win.show_about()
    assert seen[0].updates is win.updates
    from PySide6.QtCore import QEvent
    qapp.sendPostedEvents(None, QEvent.DeferredDelete)
    from shiboken6 import isValid
    assert not isValid(seen[0])                                                  # no hidden copy is left behind after each opening


def ready_window(qapp, win, fakes):
    win.updates._fetch, win.updates._prepare = fakes.fetch, fakes.prepare
    win.updates._build_plan, win.updates._launch = fakes.build_plan, fakes.launch
    win.updates.kind, win.updates.key = "installed", "macos-arm64"
    get_ready(qapp, win.updates)


def test_installing_closes_the_app_after_launching_the_helper(qapp, win, fakes, monkeypatch):
    quits = []
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: quits.append(1)))
    ready_window(qapp, win, fakes)
    win.updates.request_install()                                                # the button path: signal -> window
    assert fakes.launched == ["plan"] and quits == [1]


def test_unsaved_work_is_asked_about_before_the_app_closes_and_cancel_keeps_everything(qapp, win, fakes, monkeypatch):
    quits = []
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: quits.append(1)))
    ready_window(qapp, win, fakes)
    win.session.project_path = Path("/tmp/unsaved-demo.echemproj")                # an open project ...
    win.session.dirty = True                                                       # ... with changes that are not saved
    monkeypatch.setattr(win, "_ask_save", lambda: mw.CANCEL)
    win.install_update()
    assert fakes.launched == [] and quits == [] and win.updates.state is State.READY       # nothing happened: the work is still there
    monkeypatch.setattr(win, "_ask_save", lambda: mw.DISCARD)
    win.install_update()
    assert fakes.launched == ["plan"] and quits == [1]


def test_a_failure_to_start_the_installer_is_reported_and_the_app_stays_open(qapp, win, fakes, monkeypatch):
    quits, warned = [], []
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: quits.append(1)))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    ready_window(qapp, win, fakes)
    fakes.plan_error = updater.UpdateError("The folder /Applications is not writable for you.")
    win.install_update()
    assert quits == [] and warned and "not writable" in warned[0]


def test_closing_the_window_stops_the_worker(qapp, win, monkeypatch):
    stopped = []
    monkeypatch.setattr(win.updates, "shutdown", lambda: stopped.append(1))
    win.session.dirty = False
    win.close()
    assert stopped == [1]


def test_the_settings_page_has_the_update_switch(win):
    win.show_settings()
    box = win._settings.update_check
    assert box.isChecked()
    box.setChecked(False)
    assert prefs.get("update_check") is False
    prefs.set("update_check", True)
    win._settings._load()
    assert box.isChecked()


def test_the_new_preferences_are_validated():
    from echem_analyzer import prefs as prefs_mod
    assert prefs.get("update_check") is True and prefs.get("last_update_check") == 0.0 and prefs.get("update_seen") == ""
    assert prefs_mod.clean("update_check", "yes") is True
    assert prefs_mod.clean("last_update_check", -5) == 0.0 and prefs_mod.clean("last_update_check", float("nan")) == 0.0
    assert prefs_mod.clean("last_update_check", 1.8e9) == 1.8e9
    assert prefs_mod.clean("update_seen", 12) == ""


@pytest.mark.skipif(__import__("sys").platform != "darwin", reason="builds a real disk image with hdiutil")
def test_the_whole_update_chain_on_macos_manager_to_detached_helper_to_replaced_app(qapp, tmp_path, monkeypatch):
    """Check, download (stood in by a real disk image), then the real plan and the real detached helper replace the app and reopen it."""
    import hashlib
    import os
    import stat
    import subprocess
    import sys

    # a fake installed app, and a disk image that holds the new version
    src = tmp_path / "src" / "Echem Analyzer.app" / "Contents" / "MacOS"
    src.mkdir(parents=True)
    (src / "EchemAnalyzer").write_text("NEW 0.6.0")
    dmg = tmp_path / "Echem-Analyzer-0.6.0-macos-arm64.dmg"
    subprocess.run(["hdiutil", "create", "-volname", "t", "-srcfolder", str(tmp_path / "src"), "-ov", "-format", "UDZO", str(dmg)],
                   check=True, capture_output=True)
    target = tmp_path / "Applications" / "Echem Analyzer.app"
    (target / "Contents" / "MacOS").mkdir(parents=True)
    exe = target / "Contents" / "MacOS" / "EchemAnalyzer"
    exe.write_text("OLD 0.5.0")

    # `open` is replaced by a recorder, so no real app is launched
    bindir = tmp_path / "bin"
    bindir.mkdir()
    opened_log = tmp_path / "open.log"
    stub = bindir / "open"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{opened_log}"\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")

    gone = subprocess.Popen([sys.executable, "-c", "pass"])                      # a process that has already exited: the "app" that quit
    gone.wait()
    digest = hashlib.sha256(dmg.read_bytes()).hexdigest()
    rel = release()
    asset = updater.choose_asset(rel, "macos-arm64", "installed")
    prepared = updater.Prepared(rel, asset, dmg, digest)

    m = UpdateManager(fetch=lambda: rel, prepare=lambda *a: prepared,
                      build_plan=lambda prep: updater.build_plan(prep, "darwin", exe, pid=gone.pid), launch=updater.launch)
    m.kind, m.key = "installed", "macos-arm64"
    try:
        check_and_wait(qapp, m)
        assert m.state is State.AVAILABLE and m.can_auto_install
        m.download()
        idle(qapp, m)
        assert m.state is State.READY
        assert m.launch_installer() == "" and m.state is State.INSTALLING
        def done() -> bool:
            try:                                                                 # between the helper's two renames the path briefly does not exist
                return (target / "Contents" / "MacOS" / "EchemAnalyzer").read_text() == "NEW 0.6.0" and opened_log.exists()
            except FileNotFoundError:
                return False

        replaced = wait_until(qapp, done, timeout=60)
        log = (dmg.parent / "update.log").read_text() if (dmg.parent / "update.log").exists() else "(no log)"
        assert replaced, log
        assert opened_log.read_text().strip() == str(target) and "updated" in log
        assert not Path(str(target) + ".update-old").exists()
    finally:
        m.shutdown()
