"""Packaging: the build driver, the Debian and Windows templates, icons, the workflows and the entry point.

What cannot be tested here (building the Windows installer, running dpkg-deb, the GitHub runners) is covered by the checks CI runs on
the real packages; these tests make sure everything those steps consume is consistent."""
import configparser
import importlib.util
import json
import re
import stat
import struct
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

import echem_analyzer

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("echem_build", ROOT / "packaging" / "build.py")
build = importlib.util.module_from_spec(spec)
sys.modules["echem_build"] = build
spec.loader.exec_module(build)


# ===================================================================== version, tag, notes, names

def test_build_reads_the_package_version_and_checks_tags():
    assert build.read_version() == echem_analyzer.__version__
    assert build.tag_matches(f"v{echem_analyzer.__version__}", echem_analyzer.__version__)
    assert not build.tag_matches("v9.9.9", echem_analyzer.__version__) and not build.tag_matches(echem_analyzer.__version__, echem_analyzer.__version__)
    assert build.main(["--check-tag", f"v{echem_analyzer.__version__}"]) == 0
    assert build.main(["--check-tag", "v0.0.1"]) == 1


def test_release_notes_come_from_the_changelog(capsys):
    notes = build.release_notes("0.4.0")
    assert "First tagged release" in notes and "### Added" in notes and "## [" not in notes and "[0.4.0]: https" not in notes
    assert build.main(["--release-notes", "0.4.0"]) == 0 and "First tagged release" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="no section"):
        build.release_notes("9.9.9")


def test_artifact_names_are_stable_and_unique():
    v = "1.2.3"
    assert build.artifact_names("macos", v, "arm64") == ["Echem-Analyzer-1.2.3-macos-arm64.dmg"]
    assert build.artifact_names("macos", v, "x86_64") == ["Echem-Analyzer-1.2.3-macos-x86_64.dmg"]
    assert build.artifact_names("windows", v) == ["Echem-Analyzer-1.2.3-windows-x64-setup.exe", "Echem-Analyzer-1.2.3-windows-x64-portable.zip"]
    assert build.artifact_names("linux", v, "x86_64") == ["echem-analyzer_1.2.3_amd64.deb"]
    assert build.artifact_names("linux", v, "arm64") == ["echem-analyzer_1.2.3_arm64.deb"]
    with pytest.raises(ValueError):
        build.artifact_names("beos", v)


def test_cannot_build_for_another_operating_system():
    other = next(t for t in ("macos", "windows", "linux") if t != build.current_target())
    with pytest.raises(SystemExit, match="cannot cross-compile"):
        build.main(["--target", other])


# ===================================================================== Debian package

def test_deb_control_file():
    c = build.deb_control("0.4.0", "amd64", 123456, "2.35")
    fields = dict(line.split(": ", 1) for line in c.splitlines() if re.match(r"^[A-Z][A-Za-z-]+: ", line))
    assert fields["Package"] == "echem-analyzer" and fields["Version"] == "0.4.0" and fields["Architecture"] == "amd64"
    assert fields["Installed-Size"] == "123456" and fields["Section"] == "science" and "@" in fields["Maintainer"]
    deps = [d.strip() for d in fields["Depends"].split(",")]
    assert deps[0] == "libc6 (>= 2.35)" and {"libxcb-cursor0", "libgl1", "libegl1", "libxkbcommon-x11-0"} <= set(deps)
    assert c.endswith("\n") and "\n\n" not in c                                        # a blank line would end the stanza
    desc = c.split("Description: ", 1)[1].splitlines()[1:]
    assert desc and all(line.startswith(" ") for line in desc)                         # continuation lines start with a space


def test_desktop_entry_and_mime_type_are_valid():
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    cp.read_string(build.desktop_entry())
    d = cp["Desktop Entry"]
    assert d["Type"] == "Application" and d["Exec"] == "echem-analyzer %F" and d["Icon"] == "echem-analyzer" and d["Terminal"] == "false"
    assert d["Categories"].endswith(";") and d["MimeType"] == "application/x-echemproj;"
    ns = {"m": "http://www.freedesktop.org/standards/shared-mime-info"}
    root = ET.fromstring(build.mime_xml())
    t = root.find("m:mime-type", ns)
    assert t.get("type") == "application/x-echemproj" and t.find("m:glob", ns).get("pattern") == "*.echemproj"


def fake_app(tmp_path: Path) -> Path:
    app = tmp_path / "echem-analyzer"
    (app / "_internal" / "lib").mkdir(parents=True)
    exe = app / "echem-analyzer"
    exe.write_bytes(b"#!/bin/sh\n")
    exe.chmod(0o755)
    (app / "_internal" / "lib" / "libfoo.so").write_bytes(b"x" * 5000)
    (app / "_internal" / "lib" / "libfoo.so").chmod(0o600)                              # too restrictive: must become 0644
    (app / "_internal" / "data.txt").write_text("hello")
    return app


def test_deb_tree_layout_and_permissions(tmp_path):
    tree = build.make_deb_tree(tmp_path / "pkg", fake_app(tmp_path), "0.4.0", "amd64", glibc="2.35")
    exe = tree / "opt/echem-analyzer/echem-analyzer"
    assert exe.exists() and stat.S_IMODE(exe.stat().st_mode) == 0o755
    link = tree / "usr/bin/echem-analyzer"
    assert link.is_symlink() and str(link.readlink()) == "/opt/echem-analyzer/echem-analyzer"
    for rel in ("usr/share/applications/echem-analyzer.desktop", "usr/share/mime/packages/echem-analyzer.xml",
                "usr/share/icons/hicolor/512x512/apps/echem-analyzer.png", "usr/share/icons/hicolor/scalable/apps/echem-analyzer.svg"):
        assert (tree / rel).is_file(), rel
    for p in tree.rglob("*"):
        if p.is_symlink():
            continue
        mode = stat.S_IMODE(p.stat().st_mode)
        assert mode in (0o755, 0o644), (p, oct(mode))
        assert mode == 0o755 if p.is_dir() else mode in (0o755, 0o644)                  # directories are traversable
        assert not mode & 0o022                                                        # nothing writable by group or others
    assert stat.S_IMODE((tree / "opt/echem-analyzer/_internal/lib/libfoo.so").stat().st_mode) == 0o644
    control = (tree / "DEBIAN/control").read_text()
    assert "Version: 0.4.0" in control and "libc6 (>= 2.35)" in control
    size = int(re.search(r"Installed-Size: (\d+)", control).group(1))
    assert size >= 5 and size < 100                                                    # KiB: the 5 kB library plus a few small files


# ===================================================================== Windows installer and the spec

def test_inno_script_matches_the_build():
    iss = (ROOT / "packaging/windows/echem-analyzer.iss").read_text()
    guid = re.search(r"AppId=\{\{([0-9A-F-]{36})\}", iss).group(1)
    assert str(uuid.UUID(guid)).upper() == guid                                          # a real GUID, fixed forever
    out = re.search(r"OutputBaseFilename=(.+)", iss).group(1).replace("{#AppVersion}", "1.2.3")
    assert out + ".exe" == build.artifact_names("windows", "1.2.3")[0]
    assert '#define AppExe "EchemAnalyzer.exe"' in iss and 'ValueData: "EchemAnalyzer.Project"' in iss and ".echemproj" in iss
    assert "PrivilegesRequired=lowest" in iss and "ChangesAssociations=yes" in iss
    assert (ROOT / "packaging/windows" / re.search(r"SetupIconFile=(.+)", iss).group(1).replace("\\", "/")).resolve().is_file()
    assert "/DAppVersion" in iss.splitlines()[0]


def test_spec_and_driver_agree_on_names():
    text = (ROOT / "packaging/echem-analyzer.spec").read_text()
    assert 'EXE_NAME = "echem-analyzer" if sys.platform.startswith("linux") else "EchemAnalyzer"' in text
    assert f'APP_NAME = "{build.APP_NAME}"' in text and "echemproj" in text and "collect_submodules(\"echem_analyzer\")" in text
    assert build.frozen_executable("linux").parts[-2:] == ("echem-analyzer", "echem-analyzer")
    assert build.frozen_executable("windows").name == "EchemAnalyzer.exe"
    assert build.frozen_executable("macos").parts[-4:] == ("Echem Analyzer.app", "Contents", "MacOS", "EchemAnalyzer")
    assert (ROOT / "packaging/entry.py").is_file()


def test_icons_are_complete_and_well_formed():
    png = (ROOT / "assets/icon.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", png[16:24]) == (512, 512)
    ico = (ROOT / "assets/icon.ico").read_bytes()
    reserved, kind, n = struct.unpack("<HHH", ico[:6])
    assert (reserved, kind, n) == (0, 1, 7)
    sizes = []
    for k in range(n):
        w, h, _c, _r, _planes, _bpp, length, offset = struct.unpack("<BBBBHHII", ico[6 + 16 * k:22 + 16 * k])
        assert ico[offset:offset + 8] == b"\x89PNG\r\n\x1a\n" and offset + length <= len(ico)
        sizes.append(w or 256)
    assert sizes == [16, 24, 32, 48, 64, 128, 256]
    icns = (ROOT / "assets/icon.icns").read_bytes()
    assert icns[:4] == b"icns" and struct.unpack(">I", icns[4:8])[0] == len(icns)
    pos, kinds = 8, []
    while pos < len(icns):
        kinds.append(icns[pos:pos + 4])
        length = struct.unpack(">I", icns[pos + 4:pos + 8])[0]
        assert icns[pos + 8:pos + 16] == b"\x89PNG\r\n\x1a\n"
        pos += length
    assert pos == len(icns) and kinds == [b"icp4", b"icp5", b"icp6", b"ic07", b"ic08", b"ic09", b"ic10"]
    assert "<svg" in (ROOT / "assets/icon.svg").read_text()


# ===================================================================== workflows

def workflow(name: str) -> dict:
    doc = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
    doc["on"] = doc.pop(True, doc.get("on"))                                             # YAML reads the key `on:` as True
    return doc


def all_steps(wf: dict):
    return [s for j in wf["jobs"].values() for s in j.get("steps", [])]


def test_release_workflow_builds_every_platform_and_publishes_only_on_tags():
    wf = workflow("release.yml")
    assert wf["on"]["push"]["tags"] == ["v[0-9]+.[0-9]+.[0-9]+"] and "workflow_dispatch" in wf["on"]
    assert wf["permissions"] == {"contents": "read"}
    jobs = wf["jobs"]
    assert set(jobs) == {"verify", "build", "release"} and jobs["build"]["needs"] == "verify" and jobs["release"]["needs"] == "build"
    oses = {m["os"] for m in jobs["build"]["strategy"]["matrix"]["include"]}
    assert {"windows-latest", "ubuntu-22.04"} <= oses and sum(o.startswith("macos") for o in oses) == 2     # Apple silicon and Intel
    assert jobs["build"]["strategy"]["fail-fast"] is False
    assert jobs["release"]["if"] == "github.ref_type == 'tag'" and jobs["release"]["permissions"] == {"contents": "write"}
    assert "permissions" not in jobs["build"] and "permissions" not in jobs["verify"]       # only the release job may write
    run_text = "\n".join(s.get("run", "") for s in all_steps(wf))
    assert "packaging/build.py --check-tag" in run_text and "uv run python packaging/build.py" in run_text
    assert "--selftest" in run_text and "apt-get install -y ./dist/release/echem-analyzer_" in run_text and "/VERYSILENT" in run_text
    assert "--locked" in run_text                                                          # the lock file is what gets built


def test_every_action_is_pinned_to_a_major_version():
    for name in ("ci.yml", "release.yml"):
        for s in all_steps(workflow(name)):
            if "uses" in s:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@v\d+", s["uses"]), s["uses"]
    dep = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    assert dep["updates"][0]["package-ecosystem"] == "github-actions"


def test_ci_workflow_runs_the_tests_headless():
    wf = workflow("ci.yml")
    assert "pull_request" in wf["on"] and wf["on"]["push"]["branches"] == ["main"] and wf["permissions"] == {"contents": "read"}
    job = wf["jobs"]["test"]
    assert job["env"]["QT_QPA_PLATFORM"] == "offscreen"
    assert "uv sync --locked" in "\n".join(s.get("run", "") for s in job["steps"])


# ===================================================================== the program itself

def test_the_selftest_runs_the_whole_app_and_reports_json(qapp, tmp_path):
    from echem_analyzer.__main__ import main
    out = tmp_path / "selftest.json"
    assert main(["echem-analyzer", "--selftest", str(out)]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["ok"] and doc["version"] == echem_analyzer.__version__ and doc["frozen"] is False
    assert [s["step"] for s in doc["steps"]] == ["import every module", "analyse, calibrate, report", "write PDF, Excel and project file",
                                                 "open the window and draw every tab"] and all(s["ok"] for s in doc["steps"])


def test_a_failing_step_fails_the_selftest(qapp, tmp_path, monkeypatch):
    from echem_analyzer import selftest
    monkeypatch.setattr(selftest, "STEPS", [("fine", lambda ctx: "ok"), ("broken", lambda ctx: 1 / 0), ("never reached", lambda ctx: "x")])
    out = tmp_path / "r.json"
    assert selftest.run(str(out)) == 1
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["ok"] is False and [s["ok"] for s in doc["steps"]] == [True, False] and "ZeroDivisionError" in doc["steps"][1]["error"]


def test_double_clicking_a_file_on_macos_opens_it(qapp):
    """macOS delivers a double-clicked file as an event, before or after the window exists."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFileOpenEvent
    from types import SimpleNamespace
    from echem_analyzer.__main__ import _application_class
    cls = _application_class()
    opened = []
    early = SimpleNamespace(window=None, pending=[])
    assert cls.event(early, QFileOpenEvent("/tmp/a.echemproj")) is True and early.pending == ["/tmp/a.echemproj"]   # app still starting
    late = SimpleNamespace(window=SimpleNamespace(load_paths=lambda p: opened.append(p)), pending=[])
    assert cls.event(late, QFileOpenEvent("/tmp/b.echemproj")) is True and opened == [["/tmp/b.echemproj"]] and late.pending == []
    assert QFileOpenEvent("/x").type() == QEvent.FileOpen


# ===================================================================== checksums

def test_checksum_files_use_unix_line_endings_whatever_the_system(tmp_path, monkeypatch):
    """Windows' default text mode wrote CRLF into v0.5.0's SHA256SUMS.txt, so `shasum -c SHA256SUMS.txt` failed for the Windows files elsewhere."""
    f = tmp_path / "a.bin"
    f.write_bytes(b"x")
    seen = []
    real = Path.write_text

    def spy(self, data, encoding=None, errors=None, newline=None):
        seen.append(newline)
        return real(self, data, encoding=encoding, errors=errors, newline=newline)

    monkeypatch.setattr(Path, "write_text", spy)
    build.write_checksum(f)
    assert seen == ["\n"]                                                       # asked for LF explicitly: no system default can turn it into CRLF
    raw = (tmp_path / "a.bin.sha256").read_bytes()
    assert raw.endswith(b"  a.bin\n") and b"\r" not in raw
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "tr -d '\\r' > artifacts/SHA256SUMS.txt" in workflow                  # and the release job strips any that a runner still wrote
