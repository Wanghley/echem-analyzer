"""``python -m echem_analyzer [files or folders…]``"""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if len(argv) > 1 and argv[1] in ("--version", "-V"):
        from . import __version__
        print(f"echem-analyzer {__version__}")                   # before any Qt import: works headless, for bug reports
        return 0
    from PySide6.QtWidgets import QApplication

    app = QApplication(argv)
    app.setApplicationName("Echem Analyzer")
    app.setOrganizationName("EchemAnalyzer")

    from .gui.main_window import MainWindow

    win = MainWindow()
    win.show()
    if len(argv) > 1:
        win.load_paths(argv[1:])
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
