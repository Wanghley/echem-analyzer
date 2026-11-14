"""Command line: run the app's exports and checks without opening a window, for scripts and batch jobs.

    echem-analyzer report    PROJECT [-o report.pdf]
    echem-analyzer workbook  PROJECT [-o workbook.xlsx]
    echem-analyzer datasheet PROJECT [-q QUANTITY] [-c CONDITION] [-v VARIANT ...] [-o datasheet.pdf]
    echem-analyzer package   PROJECT [-o package.zip] [--no-raw]
    echem-analyzer qc        FILE_OR_FOLDER ... [--strict] [--json]
    echem-analyzer convert   FILE [-o curves.csv]

Exit codes: 0 done (or no problems found), 1 ``qc`` found problems, 2 an input could not be used.  Anything that draws a PDF starts
Qt on the "offscreen" platform, so no display is needed.  (The Windows build is a windowed program and shows no console output; run
the command line from a source or pip install there.)
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

COMMANDS = ("report", "workbook", "datasheet", "package", "qc", "convert")


class CliError(Exception):
    """An input could not be used; the message is printed after the program name and the exit code is 2."""


def _qt():
    """A Qt application that needs no display, for the PDF writer and the fonts of the plots."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication(["echem-analyzer"])
    app.setApplicationName("Echem Analyzer")
    app.setOrganizationName("EchemAnalyzer")
    return app


def _project(path: str):
    from .project import ProjectError, load_project
    p = Path(path)
    if not p.is_file():
        raise CliError(f"cannot read {path}: no such file")
    try:
        return load_project(p)
    except (ProjectError, OSError, ValueError) as exc:
        raise CliError(f"cannot read {path}: {exc}") from exc


def _out(given: Optional[str], source: str, suffix: str) -> Path:
    return Path(given) if given else Path(source).with_suffix(suffix)


# --------------------------------------------------------------------------- commands

def cmd_report(a: argparse.Namespace) -> int:
    data = _project(a.project)
    _qt()
    from .report import build_report
    from .report_pdf import write_pdf
    out = _out(a.output, a.project, ".pdf")
    pages = write_pdf(build_report(data), out)
    print(f"wrote {out} ({pages} pages)")
    return 0


def cmd_workbook(a: argparse.Namespace) -> int:
    data = _project(a.project)
    _qt()
    from .report import build_report
    from .workbook import export_workbook
    out = _out(a.output, a.project, ".xlsx")
    sheets = export_workbook(data, build_report(data), out)
    print(f"wrote {out} ({sheets} sheets)")
    return 0


def cmd_datasheet(a: argparse.Namespace) -> int:
    data = _project(a.project)
    _qt()
    from . import datasheet
    from . import study_analysis as sa
    from .report_pdf import write_pdf
    quantity = a.quantity or next(iter(sa.quantities(data.responses)), "")
    if not quantity:
        raise CliError(f"{a.project} has no responses to make a datasheet from")
    try:
        report = datasheet.build(data, quantity, a.condition, a.variant or None)
    except ValueError as exc:
        raise CliError(str(exc)) from exc
    out = _out(a.output, a.project, ".datasheet.pdf")
    pages = write_pdf(report, out)
    print(f"wrote {out} ({pages} pages, {len(report.sections)} variants)")
    return 0


def cmd_package(a: argparse.Namespace) -> int:
    from .package import build_package
    data = _project(a.project)
    info = build_package(data, _out(a.output, a.project, ".package.zip"), include_raw=not a.no_raw)
    print(f"wrote {info['path']} ({info['curves']} curves, {info['responses']} responses, {info['raw_included']} raw files)")
    for name in info["raw_missing"]:
        print(f"warning: the original file {name} was not found, so it is not in the package", file=sys.stderr)
    return 0


def _gather(paths: Sequence[str]):
    from . import readers
    found, problems = [], []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            targets = readers.find_files(p)
            if not targets:
                problems.append((raw, "no instrument files found in this folder"))
        elif p.is_file():
            targets = [p]
        else:
            problems.append((raw, "no such file or folder"))
            continue
        for t in targets:
            try:
                found.append(readers.load(t))
            except (ValueError, OSError) as exc:
                problems.append((str(t), str(exc)))
    return found, problems


def cmd_qc(a: argparse.Namespace) -> int:
    from . import dataqc
    files, problems = _gather(a.paths)
    if not files and not problems:
        raise CliError("nothing to check")
    flags = dataqc.check_files(files)
    rows = []
    for f in files:
        for c in f.curves:
            for fl in flags.get(c.uid, []):
                rows.append({"file": f.name, "curve": c.label, "severity": fl.severity, "code": fl.code, "message": fl.message})
    errors = sum(r["severity"] == dataqc.ERROR for r in rows) + len(problems)
    warnings = sum(r["severity"] == dataqc.WARNING for r in rows)
    curves = sum(len(f.curves) for f in files)
    if a.json:
        print(json.dumps({"files": len(files), "curves": curves, "errors": errors, "warnings": warnings, "findings": rows,
                          "unreadable": [{"file": p, "reason": r} for p, r in problems]}, indent=2, ensure_ascii=False))
    else:
        for p, reason in problems:
            print(f"{p}: error: {reason}")
        for r in rows:
            print(f"{r['file']} :: {r['curve']}: {r['severity']}: {r['message']}")
        print(f"{len(files)} files, {curves} curves: {errors} errors, {warnings} warnings")
    return 1 if errors or (a.strict and warnings) else 0


def cmd_convert(a: argparse.Namespace) -> int:
    from . import readers
    p = Path(a.file)
    if not p.is_file():
        raise CliError(f"cannot read {a.file}: no such file")
    try:
        data = readers.load(p)
    except (ValueError, OSError) as exc:
        raise CliError(f"cannot read {a.file}: {exc}") from exc
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["file", "curve", "kind", "scan", "x", "y", "x_unit", "y_unit"])
    for c in data.curves:
        for k in range(c.n):
            w.writerow([data.name, c.label, c.kind, "" if c.scan is None else c.scan, repr(float(c.x[k])), repr(float(c.y[k])), c.x_unit, c.y_unit])
    if a.output == "-":
        sys.stdout.write(buf.getvalue())
    else:
        out = _out(a.output, a.file, ".tidy.csv")
        out.write_text(buf.getvalue(), encoding="utf-8")
        print(f"wrote {out} ({len(data.curves)} curves, {sum(c.n for c in data.curves)} points)")
    return 0


# --------------------------------------------------------------------------- parsing

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="echem-analyzer", description="Run Echem Analyzer's exports and checks without a window.",
                                 epilog="Without a command the program opens its window. Use --version for the version.")
    sub = ap.add_subparsers(dest="command", metavar="command")

    def command(name: str, fn, help_: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_, description=help_)
        sp.set_defaults(fn=fn)
        return sp

    sp = command("report", cmd_report, "write the PDF report of a project")
    sp.add_argument("project")
    sp.add_argument("-o", "--output", help="PDF to write (default: next to the project)")
    sp = command("workbook", cmd_workbook, "write the Excel workbook of a project")
    sp.add_argument("project")
    sp.add_argument("-o", "--output")
    sp = command("datasheet", cmd_datasheet, "write a one-page datasheet per sensor variant")
    sp.add_argument("project")
    sp.add_argument("-q", "--quantity", help="the response to describe (default: the first in the project)")
    sp.add_argument("-c", "--condition", default="analyte")
    sp.add_argument("-v", "--variant", action="append", help="only this variant (repeat for several)")
    sp.add_argument("-o", "--output")
    sp = command("package", cmd_package, "write a self-describing data package (zip) of a project")
    sp.add_argument("project")
    sp.add_argument("-o", "--output")
    sp.add_argument("--no-raw", action="store_true", help="leave the original instrument files out")
    sp = command("qc", cmd_qc, "check instrument files for problems (exit code 1 if any are found)")
    sp.add_argument("paths", nargs="+", metavar="FILE_OR_FOLDER")
    sp.add_argument("--strict", action="store_true", help="count warnings as problems too")
    sp.add_argument("--json", action="store_true", help="print the findings as JSON")
    sp = command("convert", cmd_convert, "convert an instrument file to a tidy CSV in the app's units")
    sp.add_argument("file")
    sp.add_argument("-o", "--output", help="CSV to write, or - for standard output")
    return ap


def run(argv: Sequence[str]) -> int:
    """Run a command; ``argv`` is what follows the program name.  Returns the exit code."""
    ap = build_parser()
    try:
        args = ap.parse_args(list(argv))
    except SystemExit as exc:                                     # argparse exits itself on --help and on a usage error
        return int(exc.code or 0)
    if not getattr(args, "fn", None):
        ap.print_help()
        return 0
    try:
        return args.fn(args)
    except CliError as exc:
        print(f"echem-analyzer: {exc}", file=sys.stderr)
        return 2
