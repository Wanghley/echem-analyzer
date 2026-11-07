"""Updates: the update manager (state, worker thread), the Updates card and version information in the About window, and the update
flow in the main window.

No network and no real installer: the update manager gets fake ``fetch`` / ``prepare`` / ``launch`` functions, and every link goes to a
recorder. Jobs run on the manager's real worker thread, so these tests also prove the GUI thread is never blocked or touched.
"""

import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QThread

from echem_analyzer import __version__, theme, updater
from echem_analyzer.gui.update_manager import State, UpdateManager
from echem_analyzer.prefs import prefs


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


def test_the_new_preferences_are_validated():
    from echem_analyzer import prefs as prefs_mod
    assert prefs.get("update_check") is True and prefs.get("last_update_check") == 0.0 and prefs.get("update_seen") == ""
    assert prefs_mod.clean("update_check", "yes") is True
    assert prefs_mod.clean("last_update_check", -5) == 0.0 and prefs_mod.clean("last_update_check", float("nan")) == 0.0
    assert prefs_mod.clean("last_update_check", 1.8e9) == 1.8e9
    assert prefs_mod.clean("update_seen", 12) == ""
