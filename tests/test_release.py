"""Versioning: one source of truth, a changelog that matches it, and a CLI that reports it."""
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import echem_analyzer

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def test_version_is_semver_and_comes_from_the_package_only():
    assert SEMVER.match(echem_analyzer.__version__)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "version" not in project["project"] and project["project"]["dynamic"] == ["version"]
    assert project["tool"]["hatch"]["version"]["path"] == "echem_analyzer/__init__.py"


def test_changelog_describes_the_current_version():
    text = (ROOT / "CHANGELOG.md").read_text()
    versions = re.findall(r"^## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})$", text, re.M)
    assert versions and versions[0][0] == echem_analyzer.__version__, "newest changelog section must be the current version"
    assert "## [Unreleased]" in text and text.index("## [Unreleased]") < text.index(f"## [{versions[0][0]}]")
    nums = [tuple(map(int, v.split("."))) for v, _ in versions]
    assert nums == sorted(nums, reverse=True) and len(set(nums)) == len(nums)         # newest first, no repeats
    assert f"[{echem_analyzer.__version__}]: https://github.com/Wanghley/echem-analyzer/releases/tag/v{echem_analyzer.__version__}" in text


def test_command_line_reports_the_version_without_starting_qt():
    for flag in ("--version", "-V"):
        r = subprocess.run([sys.executable, "-I", "-c",
                            f"import sys; sys.argv=['echem_analyzer','{flag}']; from echem_analyzer.__main__ import main; "
                            "code = main(); assert not [m for m in sys.modules if m.startswith('PySide6')], 'Qt was imported'; raise SystemExit(code)"],
                           capture_output=True, text=True, cwd=str(ROOT), env={"PYTHONPATH": str(ROOT)})
        assert r.returncode == 0 and r.stdout.strip() == f"echem-analyzer {echem_analyzer.__version__}", r.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="shell script")
def test_release_script_refuses_bad_input():
    def run(*args):
        return subprocess.run([str(ROOT / "scripts" / "release.sh"), *args], capture_output=True, text=True, cwd=str(ROOT))
    assert "usage" in run().stderr and run().returncode != 0
    assert "usage" in run("1.2").stderr
    assert "unknown option" in run("9.9.9", "--bogus").stderr
