"""``python -m echem_analyzer [files, folders or a project…]``; ``--version``; ``--selftest [FILE]``."""
from __future__ import annotations

import sys


def _application_class():
    """The QApplication subclass (built lazily so that ``--version`` never imports Qt).

    macOS does not pass a double-clicked file as an argument: it sends a file-open event, also while the app is already running."""
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    class EchemApplication(QApplication):
        def __init__(self, argv):
            super().__init__(argv)
            self.window = None
            self.pending: list[str] = []

        def event(self, e):  # noqa: N802
            if e.type() == QEvent.FileOpen:
                if self.window is not None:
                    self.window.load_paths([e.file()])
                else:
                    self.pending.append(e.file())          # asked before the window existed (launched by double-click)
                return True
            return super().event(e)

    return EchemApplication


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if len(argv) > 1 and argv[1] in ("--version", "-V"):
        from . import __version__
        print(f"echem-analyzer {__version__}")                   # before any Qt import: works headless, for bug reports
        return 0
    if len(argv) > 1 and argv[1] == "--selftest":
        from .selftest import run
        return run(argv[2] if len(argv) > 2 else None)

    from PySide6.QtGui import QIcon

    from . import theme

    app = _application_class()(argv)
    app.setApplicationName("Echem Analyzer")
    app.setOrganizationName("EchemAnalyzer")
    app.setWindowIcon(QIcon(theme.app_logo(256)))

    from .gui.main_window import MainWindow

    win = MainWindow()
    app.window = win
    win.show()
    win.schedule_support_popup()                       # only here: tests and --selftest never see the card
    win.schedule_update_check()                        # ... and never touch the network
    paths = list(argv[1:]) + app.pending
    if paths:
        win.load_paths(paths)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
