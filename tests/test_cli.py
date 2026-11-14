"""The command line: exports and checks without a window."""
import csv
import io
import json
import subprocess
import sys
import zipfile

import numpy as np
import pytest

import synth
from echem_analyzer import acceptance as acc
from echem_analyzer import cli
from echem_analyzer import devices as dv
from echem_analyzer.models import ProjectData, ProjectMeta
from echem_analyzer.project import save_project
from test_variants import batch


def good_csv(path, spike=False):
    t = np.linspace(0, 100, 501)
    y = 1.0 + 0.5 * np.minimum(np.floor(t / 20), 4) + np.random.default_rng(1).normal(0, 0.01, t.size)
    if spike:
        y[-1] += 3.0
    synth.write_pstrace_csv(path, [("CA: i vs t", t, y, "s", "µA")])
    return path


def study_project(tmp_path):
    rs, devs = batch()
    extras: dict = {}
    dv.save(extras, devs)
    acc.save(extras, [acc.Rule("Peak", minimum=3.0)])
    save_project(tmp_path / "study.echemproj", ProjectData(meta=ProjectMeta(name="Ink study"), responses=rs, extras=extras))
    return tmp_path / "study.echemproj"


# ===================================================================== qc

def test_qc_is_silent_about_good_files_and_exits_one_for_a_real_problem(tmp_path, capsys):
    good = good_csv(tmp_path / "good.csv")
    assert cli.run(["qc", str(good)]) == 0
    assert capsys.readouterr().out.strip().endswith("1 files, 1 curves: 0 errors, 0 warnings")
    spiky = good_csv(tmp_path / "spiky.csv", spike=True)
    assert cli.run(["qc", str(spiky)]) == 0                                              # a spike is a warning ...
    out = capsys.readouterr().out
    assert "spiky.csv" in out and "warning" in out and "isolated spike" in out and "0 errors, 1 warnings" in out
    assert cli.run(["qc", str(spiky), "--strict"]) == 1                                  # ... unless asked to be strict
    capsys.readouterr()
    (tmp_path / "bad.csv").write_text("a,b\n1,2\n", encoding="utf-8")                    # not an instrument file at all
    assert cli.run(["qc", str(tmp_path / "bad.csv")]) == 1                               # an unreadable input is an error
    assert "error" in capsys.readouterr().out


def test_qc_takes_folders_skips_stray_files_and_can_print_json(tmp_path, capsys):
    good_csv(tmp_path / "a.csv")
    good_csv(tmp_path / "b.csv", spike=True)
    (tmp_path / "notes.txt").write_text("remember the buffer", encoding="utf-8")
    assert cli.run(["qc", str(tmp_path), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert (doc["files"], doc["curves"], doc["errors"], doc["warnings"]) == (2, 2, 0, 1)
    assert doc["findings"][0]["file"] == "b.csv" and doc["findings"][0]["code"] == "spike" and doc["unreadable"] == []
    assert cli.run(["qc", str(tmp_path / "missing")]) == 1
    assert "no such file or folder" in capsys.readouterr().out


# ===================================================================== convert

def test_convert_writes_a_tidy_csv_in_the_apps_units(tmp_path, capsys):
    e, i = synth.cv_scan(1.0, 3.0)
    synth.write_chi_txt(tmp_path / "cv.txt", "Cyclic Voltammetry", ["Potential/V", "Current/A"], list(zip(e, i * 1e-6)), {"Scan Rate (V/s)": 0.1})
    assert cli.run(["convert", str(tmp_path / "cv.txt"), "-o", str(tmp_path / "out.csv")]) == 0
    rows = list(csv.DictReader(io.StringIO((tmp_path / "out.csv").read_text(encoding="utf-8"))))
    assert rows[0].keys() == {"file", "curve", "kind", "scan", "x", "y", "x_unit", "y_unit"} and rows[0]["kind"] == "CV"
    assert rows[0]["y_unit"] == "µA" and float(rows[0]["y"]) == pytest.approx(i[0], abs=1e-6) and len(rows) == len(e)
    capsys.readouterr()
    assert cli.run(["convert", str(tmp_path / "cv.txt"), "-o", "-"]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "file,curve,kind,scan,x,y,x_unit,y_unit"
    assert cli.run(["convert", str(tmp_path / "nope.csv")]) == 2 and "no such file" in capsys.readouterr().err
    (tmp_path / "junk.dta").write_text("hello", encoding="utf-8")
    assert cli.run(["convert", str(tmp_path / "junk.dta")]) == 2 and "cannot read" in capsys.readouterr().err


# ===================================================================== project commands

def test_report_workbook_datasheet_and_package_write_their_files(qapp, tmp_path, capsys):
    project = study_project(tmp_path)
    assert cli.run(["report", str(project)]) == 0 and (tmp_path / "study.pdf").stat().st_size > 10_000
    assert cli.run(["workbook", str(project), "-o", str(tmp_path / "wb.xlsx")]) == 0 and (tmp_path / "wb.xlsx").stat().st_size > 3_000
    assert cli.run(["datasheet", str(project), "-v", "thin"]) == 0
    assert "1 variants" in capsys.readouterr().out and (tmp_path / "study.datasheet.pdf").stat().st_size > 10_000
    assert cli.run(["package", str(project), "--no-raw"]) == 0
    with zipfile.ZipFile(tmp_path / "study.package.zip") as z:
        assert "README.md" in z.namelist() and "devices.csv" in z.namelist()


def test_project_commands_explain_what_cannot_be_done(tmp_path, capsys):
    assert cli.run(["report", str(tmp_path / "missing.echemproj")]) == 2 and "no such file" in capsys.readouterr().err
    (tmp_path / "bad.echemproj").write_bytes(b"not a zip")
    assert cli.run(["workbook", str(tmp_path / "bad.echemproj")]) == 2 and "cannot read" in capsys.readouterr().err
    empty = tmp_path / "empty.echemproj"
    save_project(empty, ProjectData())
    assert cli.run(["datasheet", str(empty)]) == 2 and "no responses" in capsys.readouterr().err
    project = study_project(tmp_path)
    assert cli.run(["datasheet", str(project), "-v", "nope"]) == 2 and "no variant named" in capsys.readouterr().err


def test_without_a_command_the_help_is_shown_and_a_bad_command_line_is_a_usage_error(capsys):
    assert cli.run([]) == 0 and "report" in capsys.readouterr().out
    assert cli.run(["--help"]) == 0
    assert cli.run(["qc"]) == 2                                                          # a file is required
    assert "usage" in capsys.readouterr().err.lower()


# ===================================================================== as a program

def test_the_program_runs_commands_without_a_display_and_passes_the_exit_code_on(tmp_path):
    good_csv(tmp_path / "good.csv")
    (tmp_path / "bad.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    env = {k: v for k, v in __import__("os").environ.items() if k not in ("DISPLAY", "QT_QPA_PLATFORM")}
    run = lambda *args: subprocess.run([sys.executable, "-m", "echem_analyzer", *args], capture_output=True, text=True, env=env, timeout=120)    # noqa: E731
    ok = run("qc", str(tmp_path / "good.csv"))
    assert ok.returncode == 0 and "0 errors" in ok.stdout
    assert run("qc", str(tmp_path / "bad.csv")).returncode == 1
    project = study_project(tmp_path)
    made = run("report", str(project), "-o", str(tmp_path / "r.pdf"))
    assert made.returncode == 0, made.stderr
    assert (tmp_path / "r.pdf").stat().st_size > 10_000
    assert run("--version").stdout.startswith("echem-analyzer ")
