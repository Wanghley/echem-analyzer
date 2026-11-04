"""``python -m echem_analyzer [files or folders…]``"""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    argv = list(sys.argv if argv is None else argv)
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
