"""Readers for instrument files, one small module per vendor and one registry (no Qt).

A reader says which file suffixes it may handle, recognises its own files by their first lines (``sniff``), and turns a
file into a :class:`~echem_analyzer.models.DataFile`.  ``load`` picks the reader, so the rest of the program does not
care which instrument a file came from.  Suffixes alone are not enough: ``.txt`` is written by several instruments.

``verified`` is False for readers written from the vendor's file description with no real instrument file at hand;
the app and the documentation call those "preview".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .. import io_palmsens
from ..models import DataFile
from ._common import UnsupportedFile, read_head

__all__ = ["READERS", "Reader", "UnsupportedFile", "describe", "file_filter", "find_files", "load", "pick",
           "register", "suffixes"]


@dataclass(frozen=True)
class Reader:
    key: str                              # becomes DataFile.fmt
    name: str                             # shown to people: "Gamry Framework (.DTA)"
    suffixes: tuple[str, ...]             # lower case, with the dot
    sniff: Callable[[str], bool]          # the first characters of the file -> is it one of mine?
    load: Callable[[Path], DataFile]
    verified: bool = True


READERS: list[Reader] = []


def register(reader: Reader) -> Reader:
    if any(r.key == reader.key for r in READERS):
        raise ValueError(f"reader {reader.key!r} is already registered")
    READERS.append(reader)
    return reader


def suffixes() -> tuple[str, ...]:
    """Every file suffix some reader may handle, in registry order."""
    seen: dict[str, None] = {}
    for r in READERS:
        for s in r.suffixes:
            seen.setdefault(s, None)
    return tuple(seen)


def pick(path: str | Path) -> Optional[Reader]:
    """The reader that recognises this file, or None."""
    path = Path(path)
    candidates = [r for r in READERS if path.suffix.lower() in r.suffixes]
    if not candidates:
        return None
    try:
        head = read_head(path)
    except OSError:
        return None
    return next((r for r in candidates if r.sniff(head)), None)


def load(path: str | Path) -> DataFile:
    path = Path(path)
    reader = pick(path)
    if reader is None:
        raise UnsupportedFile(f"{path.name}: not a file any instrument reader recognises")
    return reader.load(path)


def find_files(folder: str | Path) -> list[Path]:
    """The files of a folder some reader recognises (a stray notes.txt or results.csv is left out), sorted by name."""
    folder = Path(folder)
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and not p.name.startswith(("~$", ".")) and pick(p) is not None)


def file_filter() -> str:
    """Qt file-dialog filter for the Open dialog."""
    return f"Instrument files ({' '.join('*' + s for s in suffixes())});;All files (*)"


def describe() -> list[tuple[str, tuple[str, ...], bool]]:
    """(name, suffixes, verified) of every reader, for the documentation and the Guide."""
    return [(r.name, r.suffixes, r.verified) for r in READERS]


# --------------------------------------------------------------------------- PalmSens (the original two)

def _is_palmsens_csv(head: str) -> bool:
    # PSTrace CSV: UTF-16, a "Date and time:" line first and a "Date and time measurement:" row above the units
    return bool(re.search(r"(?im)^\s*\"?date and time measurement", head)) or head.lstrip("﻿").lower().startswith("date and time")


register(Reader("csv", "PalmSens PSTrace CSV", (".csv",), _is_palmsens_csv, io_palmsens.load_csv))
register(Reader("pssession", "PalmSens PSTrace session", (".pssession",), lambda head: True, io_palmsens.load_pssession))


# --------------------------------------------------------------------------- other instruments
# Each module registers its own reader when imported (they need Reader and register, defined above).
from . import gamry  # noqa: E402,F401
