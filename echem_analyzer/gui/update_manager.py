"""The window's one source of truth about updates: what was found, what is downloading, what is ready to install.

The About window, the toolbar badge and the start page all read this object, so they never disagree. The work (talking to GitHub,
downloading, verifying) runs on a worker thread in the worker-object pattern: the worker only emits signals, and every state
change and every widget update happens on the GUI thread.  The logic itself lives in :mod:`echem_analyzer.updater`.
"""
from __future__ import annotations

import enum
import threading
import time
from typing import Callable, Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot

from .. import updater
from ..prefs import prefs

CHECK_INTERVAL = 24 * 3600            # the automatic check at startup runs at most once a day


class State(enum.Enum):
    IDLE = "idle"
    CHECKING = "checking"
    UP_TO_DATE = "up_to_date"
    AVAILABLE = "available"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    READY = "ready"
    INSTALLING = "installing"
    ERROR = "error"


BUSY = (State.CHECKING, State.DOWNLOADING, State.VERIFYING, State.INSTALLING)


class _Job(QObject):
    """Runs one function on the worker thread and reports through signals only."""
    progress = Signal(int, int)
    stage = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, fn: Callable):
        super().__init__()
        self._fn = fn
        self._cancel = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            result = self._fn(self.progress.emit, self.stage.emit, self._cancel.is_set)
        except updater.Cancelled:
            self.cancelled.emit()
        except Exception as exc:                                    # noqa: BLE001: whatever went wrong becomes a message
            self.failed.emit(updater.describe_error(exc))
        else:
            self.succeeded.emit(result)

    def cancel(self) -> None:
        self._cancel.set()


class UpdateManager(QObject):
    state_changed = Signal()
    progress_changed = Signal(int, int)
    install_requested = Signal()          # the user pressed "Install and restart": the window asks about unsaved work, then calls launch_installer

    def __init__(self, parent: Optional[QObject] = None, fetch=None, prepare=None, build_plan=None, launch=None,
                 now: Callable[[], float] = time.time):
        super().__init__(parent)
        self._fetch = fetch or updater.fetch_latest
        self._prepare = prepare or updater.prepare
        self._build_plan = build_plan or updater.build_plan
        self._launch = launch or updater.launch
        self._now = now
        self.state = State.IDLE
        self.release: Optional[updater.Release] = None
        self.asset: Optional[updater.Asset] = None
        self.prepared: Optional[updater.Prepared] = None
        self.error = ""
        self.done = 0
        self.total = 0
        self.kind = updater.install_kind()                          # installed | portable | source
        self.key = updater.platform_key()
        self._manual = False
        self._running: list[tuple[QThread, _Job]] = []

    # ------------------------------------------------------------------ what the UI shows
    @property
    def busy(self) -> bool:
        return self.state in BUSY

    @property
    def can_auto_install(self) -> bool:
        return self.asset is not None

    @property
    def last_checked(self) -> float:
        return float(prefs.get("last_update_check"))

    def hint_version(self) -> Optional[str]:
        """The newer version found earlier (kept in the preferences), if it is still newer than this one."""
        v = updater.parse_version(prefs.get("update_seen"))
        return updater.format_version(v) if v and updater.is_newer(v) else None

    def badge_text(self) -> str:
        """Short text for the toolbar badge and the start page; empty when there is nothing to announce."""
        if self.state in (State.AVAILABLE, State.DOWNLOADING, State.VERIFYING, State.READY) and self.release:
            v = self.release.text
            return {State.READY: "Update ready · restart to install", State.DOWNLOADING: f"Downloading {v}…",
                    State.VERIFYING: f"Verifying {v}…"}.get(self.state, f"Update available · v{v}")
        if self.state in (State.IDLE, State.CHECKING, State.ERROR) and self.hint_version():
            return f"Update available · v{self.hint_version()}"
        return ""

    def manual_install_note(self) -> str:
        """Why this copy cannot update itself (shown with the download page link), or an empty string if it can."""
        if self.can_auto_install:
            return ""
        if self.kind == "source":
            return "You are running from source: update with git, or install the app from the release page."
        if self.kind == "portable":
            return "This is the portable version: download the new one from the release page and replace this folder."
        if self.key is None:
            return "There is no installer for this kind of computer: use the release page."
        return "This release has no installer for this system: use the release page."

    # ------------------------------------------------------------------ actions
    def startup_check(self) -> None:
        """The automatic check: only if the user allows it, at most once a day, and sooner if an update is already known."""
        if not prefs.get("update_check") or self.busy:
            return
        if self.hint_version() or self._now() - self.last_checked > CHECK_INTERVAL:
            self.check(manual=False)

    def check(self, manual: bool = True) -> None:
        if self.busy:
            return
        self._manual = manual
        self.error = ""
        self._set(State.CHECKING)
        self._start(lambda progress, stage, cancelled: self._fetch(), self._on_checked)

    def download(self) -> None:
        """Download and verify the installer for this system (the update is only started later, by the user)."""
        if self.state is not State.AVAILABLE or self.release is None or self.asset is None:
            return
        updater.cleanup_stale()
        release, asset = self.release, self.asset
        self.done = self.total = 0
        self.error = ""
        self._set(State.DOWNLOADING)
        self._start(lambda progress, stage, cancelled: self._prepare(release, asset, progress, cancelled, stage), self._on_prepared)

    def cancel(self) -> None:
        for _thread, job in self._running:
            job.cancel()

    def retry(self) -> None:
        """After an error: try the download again if a release is known, otherwise check again."""
        if self.release and self.asset:
            self._set(State.AVAILABLE)
            self.download()
        else:
            self.check(manual=True)

    def request_install(self) -> None:
        if self.state is State.READY:
            self.install_requested.emit()

    def launch_installer(self) -> str:
        """Start the helper that installs the downloaded update after this program exits. Returns an error message, or "" on success."""
        if self.state is not State.READY or self.prepared is None:
            return "There is no downloaded update to install."
        try:
            self._launch(self._build_plan(self.prepared))
        except Exception as exc:                                    # noqa: BLE001
            self.error = updater.describe_error(exc)
            self._set(State.ERROR)
            return self.error
        self._set(State.INSTALLING)
        return ""

    def shutdown(self) -> None:
        """Stop any running job and wait for its thread: a QThread destroyed while running would crash the program."""
        for thread, job in list(self._running):
            job.cancel()
            thread.quit()
            if not thread.wait(2500):
                thread.terminate()                                  # a connection that never answers must not keep the app from closing
                thread.wait(1000)
        self._running.clear()

    # ------------------------------------------------------------------ the worker thread
    def _start(self, fn: Callable, on_ok: Callable) -> None:
        thread, job = QThread(), _Job(fn)
        job.moveToThread(thread)
        thread.started.connect(job.run)
        job.progress.connect(self._on_progress)
        job.stage.connect(self._on_stage)
        job.succeeded.connect(on_ok)
        job.failed.connect(self._on_failed)
        job.cancelled.connect(self._on_cancelled)
        for done in (job.succeeded, job.failed, job.cancelled):
            done.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)
        self._running.append((thread, job))
        thread.start()

    @Slot()
    def _on_thread_finished(self) -> None:
        finished = self.sender()
        for entry in list(self._running):
            if entry[0] is finished:
                self._running.remove(entry)
                entry[1].deleteLater()
                entry[0].deleteLater()

    @Slot(int, int)
    def _on_progress(self, done: int, total: int) -> None:
        self.done, self.total = done, total
        self.progress_changed.emit(done, total)

    @Slot(str)
    def _on_stage(self, stage: str) -> None:
        if stage == "verify":
            self._set(State.VERIFYING)

    @Slot(object)
    def _on_checked(self, release) -> None:
        self.release = release
        prefs.set("last_update_check", self._now())
        if updater.is_newer(release.version):
            self.asset = updater.choose_asset(release, self.key, self.kind)
            prefs.set("update_seen", release.text)
            self._set(State.AVAILABLE)
        else:
            self.asset = None
            prefs.set("update_seen", "")
            self._set(State.UP_TO_DATE)

    @Slot(object)
    def _on_prepared(self, prepared) -> None:
        self.prepared = prepared
        self._set(State.READY)

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        if self.state is State.CHECKING and not self._manual:
            self._set(State.IDLE)                                   # a check nobody asked for fails quietly
            return
        self.error = message
        self._set(State.ERROR)

    @Slot()
    def _on_cancelled(self) -> None:
        self._set(State.AVAILABLE if self.release and self.asset else State.IDLE)

    def _set(self, state: State) -> None:
        self.state = state
        self.state_changed.emit()
