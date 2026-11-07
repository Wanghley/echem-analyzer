#!/usr/bin/env python3
"""Build the Echem Analyzer installer for the operating system this runs on.

    uv sync --group build
    uv run python packaging/build.py                      # build, smoke-test and package for this OS
    uv run python packaging/build.py --no-installer       # only the frozen app (fastest check)
    uv run python packaging/build.py --check-tag v0.4.0   # exit 1 unless the tag matches __version__ (used by CI)
    uv run python packaging/build.py --release-notes 0.4.0   # print that version's CHANGELOG section (used by CI)

The same script runs on a developer machine and in GitHub Actions, so what works locally is what CI builds.

Steps: PyInstaller freezes the app -> the frozen app is smoke-tested (`--version`, then `--selftest`, which analyses, reports,
saves and draws every tab without a display) -> the platform installer is made, with a SHA-256 file next to each artifact:

    macOS    Echem-Analyzer-<v>-macos-<arm64|x86_64>.dmg        (.app + an Applications shortcut; hdiutil)
    Windows  Echem-Analyzer-<v>-windows-x64-setup.exe           (Inno Setup, if iscc is installed)
             Echem-Analyzer-<v>-windows-x64-portable.zip        (unzip and run, no install)
    Linux    echem-analyzer_<v>_<amd64|arm64>.deb               (dpkg-deb; for Debian, Ubuntu and derivatives)
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import math
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD = ROOT / "build"
RELEASE = DIST / "release"
SPEC = ROOT / "packaging" / "echem-analyzer.spec"

APP_NAME = "Echem Analyzer"
PACKAGE = "echem-analyzer"
HOMEPAGE = "https://github.com/Wanghley/echem-analyzer"
MAINTAINER = "Wanghley Soares Martins <me@wanghley.com>"
SUMMARY = "Electrochemistry analysis for sensor development"
DESCRIPTION = (
    "Calibration and figures of merit (LOD, LOQ, linear range), standard addition, cyclic and pulse voltammetry,\n"
    "impedance, potentiometry and electrode characterisation from PalmSens and other instruments, with PDF reports,\n"
    "Excel workbooks and one-file projects."
)

# Libraries Qt's X11 (xcb) platform plugin loads at run time, which the frozen app does not carry
DEB_DEPENDS = [
    "libgl1", "libegl1", "libfontconfig1", "libdbus-1-3", "libglib2.0-0", "libx11-xcb1", "libxkbcommon0",
    "libxkbcommon-x11-0", "libxcb-cursor0", "libxcb-icccm4", "libxcb-image0", "libxcb-keysyms1", "libxcb-randr0",
    "libxcb-render-util0", "libxcb-shape0", "libxcb-sync1", "libxcb-xfixes0", "libxcb-xinerama0", "libxcb-xkb1",
]


# =============================================================================== version, tag, release notes

def read_version() -> str:
    m = re.search(r'__version__ = "([^"]+)"', (ROOT / "echem_analyzer" / "__init__.py").read_text(encoding="utf-8"))
    if not m:
        raise SystemExit("cannot read __version__")
    return m.group(1)


def tag_matches(tag: str, version: str) -> bool:
    return tag == f"v{version}"


def release_notes(version: str) -> str:
    """The CHANGELOG.md section of ``version`` (without its heading)."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|^\[[^\]]+\]: |\Z)", text, re.S | re.M)
    if not m or not m.group(1).strip():
        raise SystemExit(f"CHANGELOG.md has no section for [{version}]")
    return m.group(1).strip() + "\n"


# =============================================================================== artifact names

def arch_name() -> str:
    return {"AMD64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())


DEB_ARCH = {"x86_64": "amd64", "arm64": "arm64"}


def deb_arch() -> str:
    return DEB_ARCH.get(arch_name(), arch_name())


def artifact_names(target: str, version: str, arch: str = "") -> list[str]:
    arch = arch or arch_name()
    if target == "macos":
        return [f"Echem-Analyzer-{version}-macos-{arch}.dmg"]
    if target == "windows":
        return [f"Echem-Analyzer-{version}-windows-x64-setup.exe", f"Echem-Analyzer-{version}-windows-x64-portable.zip"]
    if target == "linux":
        return [f"{PACKAGE}_{version}_{DEB_ARCH.get(arch, arch)}.deb"]
    raise ValueError(f"unknown target {target!r}")


def current_target() -> str:
    return {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")


# =============================================================================== helpers

def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_checksum(path: Path) -> None:
    # newline="\n": Windows' default text mode would write CRLF, and `shasum -c SHA256SUMS.txt` then looks for a file name ending in a carriage return
    (path.parent / (path.name + ".sha256")).write_text(f"{sha256(path)}  {path.name}\n", encoding="utf-8", newline="\n")


def dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


# =============================================================================== freeze and smoke-test

def freeze() -> None:
    for d in (DIST, BUILD):
        shutil.rmtree(d, ignore_errors=True)
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", DIST, "--workpath", BUILD / "pyinstaller", SPEC])


def frozen_executable(target: str) -> Path:
    if target == "macos":
        return DIST / f"{APP_NAME}.app" / "Contents" / "MacOS" / "EchemAnalyzer"
    if target == "windows":
        return DIST / "EchemAnalyzer" / "EchemAnalyzer.exe"
    return DIST / PACKAGE / PACKAGE


def smoke_test(exe: Path, target: str, version: str) -> None:
    """Run the frozen program: its version, then the whole self-test without a display."""
    if not exe.exists():
        raise SystemExit(f"the frozen program is missing: {exe}")
    if target != "windows":                         # a windowed Windows program has no console to print to
        out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=120)
        print(out.stdout.strip(), flush=True)
        if out.returncode != 0 or out.stdout.strip() != f"echem-analyzer {version}":
            raise SystemExit(f"--version gave {out.stdout!r} {out.stderr!r} (expected 'echem-analyzer {version}')")
    with tempfile.TemporaryDirectory() as tmp:
        result = Path(tmp) / "selftest.json"
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        proc = subprocess.run([str(exe), "--selftest", str(result)], env=env, capture_output=True, text=True, timeout=600)
        if not result.exists():
            raise SystemExit(f"the self-test wrote no result (exit {proc.returncode})\n{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}")
        doc = json.loads(result.read_text(encoding="utf-8"))
    for s in doc["steps"]:
        print(f"  {'ok  ' if s['ok'] else 'FAIL'} {s['step']}: {s.get('detail') or s.get('error', '')}", flush=True)
    if not doc["ok"] or proc.returncode != 0 or not doc["frozen"]:
        raise SystemExit("the self-test of the frozen program failed")
    print(f"self-test passed ({doc['version']}, frozen={doc['frozen']})", flush=True)


# =============================================================================== macOS

def package_macos(version: str) -> list[Path]:
    app = DIST / f"{APP_NAME}.app"
    out = RELEASE / artifact_names("macos", version)[0]
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "dmg"
        stage.mkdir()
        shutil.copytree(app, stage / app.name, symlinks=True)
        (stage / "Applications").symlink_to("/Applications")
        run(["hdiutil", "create", "-volname", f"{APP_NAME} {version}", "-srcfolder", stage, "-ov", "-format", "UDZO", out])
    return [out]


# =============================================================================== Windows

def find_iscc() -> str | None:
    found = shutil.which("iscc") or shutil.which("ISCC")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            for sub in ("Inno Setup 6", r"Programs\Inno Setup 6"):
                cand = Path(base) / sub / "ISCC.exe"
                if cand.exists():
                    return str(cand)
    return None


def package_windows(version: str) -> list[Path]:
    src = DIST / "EchemAnalyzer"
    names = artifact_names("windows", version)
    made: list[Path] = []
    zip_path = RELEASE / names[1]
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                z.write(p, Path("Echem-Analyzer") / p.relative_to(src))
    made.append(zip_path)
    iscc = find_iscc()
    if iscc is None:
        print("Inno Setup (iscc) not found: only the portable zip was made (choco install innosetup, or https://jrsoftware.org/isdl.php)")
        return made
    run([iscc, f"/DAppVersion={version}", f"/DSourceDir={src}", f"/DOutDir={RELEASE}", ROOT / "packaging" / "windows" / "echem-analyzer.iss"])
    made.insert(0, RELEASE / names[0])
    return made


# =============================================================================== Debian / Ubuntu

def glibc_version() -> str:
    lib, ver = platform.libc_ver()
    return ver if lib == "glibc" and ver else "2.31"


def deb_control(version: str, arch: str, installed_kib: int, glibc: str) -> str:
    desc = "\n".join(" " + (line if line.strip() else ".") for line in DESCRIPTION.splitlines())
    return (
        f"Package: {PACKAGE}\nVersion: {version}\nSection: science\nPriority: optional\nArchitecture: {arch}\n"
        f"Maintainer: {MAINTAINER}\nInstalled-Size: {installed_kib}\n"
        f"Depends: libc6 (>= {glibc}), {', '.join(DEB_DEPENDS)}\nHomepage: {HOMEPAGE}\n"
        f"Description: {SUMMARY}\n{desc}\n"
    )


def desktop_entry() -> str:
    return (
        "[Desktop Entry]\nType=Application\nVersion=1.0\n"
        f"Name={APP_NAME}\nGenericName=Electrochemistry analysis\nComment={SUMMARY}\n"
        f"Exec={PACKAGE} %F\nIcon={PACKAGE}\nTerminal=false\nCategories=Science;Chemistry;Education;\n"
        "MimeType=application/x-echemproj;\nKeywords=electrochemistry;voltammetry;impedance;calibration;sensor;\n"
    )


def mime_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">\n'
        '  <mime-type type="application/x-echemproj">\n'
        "    <comment>Echem Analyzer project</comment>\n"
        '    <glob pattern="*.echemproj"/>\n'
        f'    <icon name="{PACKAGE}"/>\n'
        "  </mime-type>\n"
        "</mime-info>\n"
    )


def _set_modes(root: Path) -> None:
    """Directories 0755, executables 0755, everything else 0644 (a .deb installs exactly these)."""
    for p in [root, *root.rglob("*")]:
        if p.is_symlink():
            continue
        if p.is_dir():
            p.chmod(0o755)
        else:
            p.chmod(0o755 if p.stat().st_mode & stat.S_IXUSR else 0o644)


def make_deb_tree(tree: Path, app_dir: Path, version: str, arch: str, glibc: str | None = None) -> Path:
    """Lay out the package: the frozen app under /opt, a launcher in /usr/bin, menu entry, icons and the file type."""
    shutil.rmtree(tree, ignore_errors=True)
    opt = tree / "opt" / PACKAGE
    shutil.copytree(app_dir, opt, symlinks=True)
    (tree / "usr" / "bin").mkdir(parents=True)
    (tree / "usr" / "bin" / PACKAGE).symlink_to(f"/opt/{PACKAGE}/{PACKAGE}")
    for rel, text in ((f"usr/share/applications/{PACKAGE}.desktop", desktop_entry()),
                      (f"usr/share/mime/packages/{PACKAGE}.xml", mime_xml())):
        f = tree / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    icons = tree / "usr" / "share" / "icons" / "hicolor"
    for sub, src in (("512x512/apps", "icon.png"), ("scalable/apps", "icon.svg")):
        (icons / sub).mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / "assets" / src, icons / sub / f"{PACKAGE}{Path(src).suffix}")
    _set_modes(tree)
    debian = tree / "DEBIAN"
    debian.mkdir()
    kib = math.ceil(dir_size(tree) / 1024)
    (debian / "control").write_text(deb_control(version, arch, kib, glibc or glibc_version()), encoding="utf-8")
    debian.chmod(0o755)
    (debian / "control").chmod(0o644)
    return tree


def package_linux(version: str) -> list[Path]:
    out = RELEASE / artifact_names("linux", version)[0]
    with tempfile.TemporaryDirectory() as tmp:
        tree = make_deb_tree(Path(tmp) / "pkg", DIST / PACKAGE, version, deb_arch())
        run(["dpkg-deb", "--root-owner-group", "--build", tree, out])
    run(["dpkg-deb", "--info", out])
    return [out]


# =============================================================================== main

PACKAGERS = {"macos": package_macos, "windows": package_windows, "linux": package_linux}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--target", choices=["auto", "macos", "windows", "linux"], default="auto")
    ap.add_argument("--no-installer", action="store_true", help="freeze and smoke-test only")
    ap.add_argument("--skip-selftest", action="store_true")
    ap.add_argument("--check-tag", metavar="TAG", help="exit 1 unless TAG is v<__version__>")
    ap.add_argument("--release-notes", metavar="VERSION", help="print the CHANGELOG section of VERSION")
    args = ap.parse_args(argv)

    version = read_version()
    if args.check_tag:
        if not tag_matches(args.check_tag, version):
            print(f"tag {args.check_tag} does not match __version__ {version} (expected v{version})", file=sys.stderr)
            return 1
        print(f"tag {args.check_tag} matches version {version}")
        return 0
    if args.release_notes:
        sys.stdout.write(release_notes(args.release_notes))
        return 0

    target = current_target() if args.target == "auto" else args.target
    if target != current_target():
        raise SystemExit(f"build the {target} package on {target}: PyInstaller cannot cross-compile (this is {current_target()})")
    print(f"Echem Analyzer {version} for {target} ({arch_name()})")
    freeze()
    if not args.skip_selftest:
        smoke_test(frozen_executable(target), target, version)
    if args.no_installer:
        return 0
    RELEASE.mkdir(parents=True, exist_ok=True)
    artifacts = PACKAGERS[target](version)
    for a in artifacts:
        write_checksum(a)
        print(f"-> {a.relative_to(ROOT)}  {a.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
