"""Helpers shared by the GUI tests."""
import os
from pathlib import Path

from PySide6.QtCore import QSettings

from echem_analyzer.gui.main_window import MainWindow
from echem_analyzer.gui.session import Session

FILES = [
    "2026-11-02 A6 GY data.pssession",
    "2026-11-02 B2 GY data.csv",
    "2026-11-02 B2 GY data.pssession",
    "2026-11-02 B5 CSF C1 data.csv",
]


def make_window(folder: Path) -> MainWindow:
    """A window whose settings (and presets) live in ``folder`` - never in the user's real config."""
    folder.mkdir(parents=True, exist_ok=True)
    qs = QSettings(str(folder / "settings.ini"), QSettings.IniFormat)
    return MainWindow(Session(qs))


def find_ca(win: MainWindow, filename: str, n_pts: int | None = None):
    for c in win.session.ca_curves():
        if Path(c.source).name == filename and (n_pts is None or c.n == n_pts):
            return c
    raise AssertionError(f"no CA run in {filename}")


def find_cv_run(win: MainWindow, filename: str):
    for r in win.session.cv_runs():
        if Path(r.source).name == filename:
            return r
    raise AssertionError(f"no CV run in {filename}")


def shot(win, qapp, name):
    """Save a screenshot when ECHEM_SHOT_DIR is set (Qt resolves nested layouts over several passes)."""
    out = os.environ.get("ECHEM_SHOT_DIR")
    if not out:
        return
    win.resize(1560, 960)
    win.show()
    for _ in range(6):
        qapp.processEvents()
    win.grab().save(str(Path(out) / f"{name}.png"))


def fill_additions(ca, stock=505.1, v0=10.0, vadd=10.0):
    b = ca._binds
    b["stock_conc"].setValue(stock)
    b["v0_ml"].setValue(v0)
    b["v_add_ul"].setValue(vadd)
    ca._apply_vadd_all()
