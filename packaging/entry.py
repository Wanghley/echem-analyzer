"""PyInstaller entry point (the frozen app starts here)."""
import multiprocessing

from echem_analyzer.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
