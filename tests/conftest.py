import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # no display needed for GUI tests

import pytest

PROJECT = Path(__file__).resolve().parents[1]
DATA_DIR = (
    PROJECT.parents[1] / "Fabrication & Profilometry" / "2026-11-02 LPA GY CSF data"
)


@pytest.fixture(scope="session")
def data_dir() -> Path:
    if not DATA_DIR.is_dir():
        pytest.skip(f"real data folder not found: {DATA_DIR}")
    return DATA_DIR


import shutil  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

from helpers import FILES  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def workdir(tmp_path, data_dir) -> Path:
    """Temporary copies of four real data files, so GUI tests never touch the real data folder."""
    d = tmp_path / "data"
    d.mkdir()
    for n in FILES:
        shutil.copy(data_dir / n, d / n)
    return d


import sys  # noqa: E402
import traceback  # noqa: E402


@pytest.fixture(autouse=True)
def fail_on_exceptions_in_qt_slots():
    """PySide6 reports an exception raised inside a slot via ``sys.excepthook`` and then carries on, so a
    broken redraw would leave the test green. Collect them and fail the test instead."""
    caught: list[str] = []
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        caught.append("".join(traceback.format_exception(exc_type, exc, tb)))

    sys.excepthook = hook
    yield
    sys.excepthook = previous
    if caught:
        pytest.fail("exception(s) raised inside a Qt slot:\n" + "\n---\n".join(caught[:3]))


import gc  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_preferences(tmp_path_factory):
    """Preferences live in a temp file for every test and start from the defaults, so a test can neither read nor
    change the developer's real settings, and a plot-style test cannot leak into the next test."""
    from echem_analyzer import report
    from echem_analyzer.prefs import prefs
    prefs.use_path(tmp_path_factory.mktemp("prefs") / "preferences.json")
    report.clear_figure_cache()
    yield
    changed = not prefs.is_default()
    prefs.use_path(tmp_path_factory.mktemp("prefs") / "preferences.json")
    if changed:                                    # a plot-style test must not leave its rcParams / palette behind
        from echem_analyzer import theme
        theme.refresh_plots()


@pytest.fixture(autouse=True)
def delete_windows_after_each_test():
    """Windows left alive by a test keep listening to the global theme / session signals, so every later test would
    redraw them too (cost grows with the number of tests). Delete them without triggering the unsaved-changes prompt."""
    yield
    app = QApplication.instance()
    if app is None:
        return
    from PySide6.QtCore import QEvent
    for w in app.topLevelWidgets():
        w.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()
