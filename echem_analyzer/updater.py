"""Find, download, verify and install a newer Echem Analyzer from GitHub Releases (no Qt: testable without a display).

How an update goes, and what each step protects against
-------------------------------------------------------
1. ``fetch_latest`` asks the GitHub API for the newest stable release (HTTPS, size-capped, nothing sent but the app version).
2. ``prepare`` downloads the installer for this system *and* the release's ``SHA256SUMS.txt``, and refuses to go on unless the
   file's SHA-256 matches. Downloads are only accepted from ``github.com`` and GitHub's own download hosts, over HTTPS, also
   after redirects. The checksum catches a damaged or truncated download; it does **not** prove who built the file, because it
   comes from the same release (the installers are not code-signed).
3. ``build_plan`` / ``launch`` start a small detached helper script (not the app, whose files the installer replaces). It waits
   for the app to exit, re-checks the checksum, installs, and starts the app again. If anything fails it starts the old
   version again, so the user is never left without a working app. Paths and the checksum are passed as arguments, never
   spliced into the script text.

On Linux the privileged step copies the package into a root-owned folder before checking and installing it, so another process
cannot swap the file between the check and the install.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import __version__

REPO = "Wanghley/echem-analyzer"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
USER_AGENT = f"echem-analyzer/{__version__}"
DOWNLOAD_PATH = f"/{REPO}/releases/download/"
SUMS_NAME = "SHA256SUMS.txt"

MAX_API_BYTES = 2_000_000
MAX_SUMS_BYTES = 64_000
MAX_DOWNLOAD_BYTES = 1_500_000_000
MAX_NOTES_CHARS = 20_000
CHUNK = 256 * 1024
STALE_DAYS = 3

Progress = Callable[[int, int], None]


class UpdateError(Exception):
    """A failure whose message is fit to show the user as it is."""


class Cancelled(Exception):
    """The user cancelled the download."""


# =============================================================================== versions

_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


def parse_version(text) -> Optional[tuple[int, int, int]]:
    """``"0.6.0"`` or ``"v0.6.0"`` as ``(0, 6, 0)``; None for anything else (release candidates, junk)."""
    m = _VERSION.fullmatch(str(text).strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def format_version(v: tuple[int, int, int]) -> str:
    return ".".join(str(n) for n in v)


def current_version() -> tuple[int, int, int]:
    return parse_version(__version__) or (0, 0, 0)


def is_newer(remote: tuple[int, int, int], local: Optional[tuple[int, int, int]] = None) -> bool:
    return remote > (local or current_version())


# =============================================================================== releases

@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int


@dataclass(frozen=True)
class Release:
    version: tuple[int, int, int]
    tag: str
    title: str
    notes: str
    page: str
    published: str
    assets: tuple[Asset, ...]

    @property
    def text(self) -> str:
        return format_version(self.version)

    def asset(self, name: str) -> Optional[Asset]:
        return next((a for a in self.assets if a.name == name), None)


_ASSET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,150}")


def check_download_url(url: str) -> str:
    """``url`` if it is a release download from this project on github.com; else an UpdateError."""
    u = urllib.parse.urlsplit(url)
    bad = (u.scheme != "https" or u.hostname != "github.com" or u.username or u.password or u.port not in (None, 443)
           or not u.path.startswith(DOWNLOAD_PATH) or ".." in u.path.split("/") or u.query or u.fragment)
    if bad:
        raise UpdateError("The release points to a download that is not from this project's GitHub releases, so it was ignored.")
    return url


def redirect_allowed(url: str) -> bool:
    """GitHub serves release files from its own hosts after a redirect; anything else is refused."""
    u = urllib.parse.urlsplit(url)
    host = u.hostname or ""
    return (u.scheme == "https" and not u.username and not u.password and u.port in (None, 443)
            and (host == "github.com" or host.endswith(".githubusercontent.com")))


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not redirect_allowed(newurl):
            raise UpdateError("The download was redirected somewhere that is not GitHub, so it was stopped.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


SYSTEM_CA_FILES = ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt")


def ca_bundle() -> Optional[str]:
    """A file of trusted root certificates: the one shipped with the app (certifi), else the system's.

    A built app cannot rely on the platform: its OpenSSL looks where the machine that built it kept certificates (for the
    macOS app, a python.org install that users do not have), so on its own it trusts nothing and every HTTPS request fails
    with "unable to get local issuer certificate"."""
    try:
        import certifi
        if os.path.isfile(certifi.where()):
            return certifi.where()
    except Exception:                                           # noqa: BLE001: no certifi: look for the system's list
        pass
    return next((p for p in SYSTEM_CA_FILES if os.path.isfile(p)), None)


def ssl_context() -> ssl.SSLContext:
    """The system's certificates (also the Windows store, which TLS-inspecting proxies add to) plus the bundled list."""
    ctx = ssl.create_default_context()
    cafile = ca_bundle()
    if cafile:
        try:
            ctx.load_verify_locations(cafile=cafile)
        except (OSError, ssl.SSLError):
            pass
    return ctx


def default_opener():
    return urllib.request.build_opener(_SafeRedirect, urllib.request.HTTPSHandler(context=ssl_context()))


def parse_release(doc) -> Release:
    """Validate the GitHub API's release object; assets that fail the checks are dropped, not trusted."""
    if not isinstance(doc, dict):
        raise UpdateError("GitHub sent an answer the app could not read.")
    if doc.get("draft") or doc.get("prerelease"):
        raise UpdateError("There is no stable release yet.")
    version = parse_version(doc.get("tag_name", ""))
    if version is None:
        raise UpdateError("The newest release has a version number the app does not understand.")
    assets = []
    for a in doc.get("assets") or []:
        if not isinstance(a, dict):
            continue
        name, url, size = a.get("name"), a.get("browser_download_url"), a.get("size")
        if not (isinstance(name, str) and _ASSET_NAME.fullmatch(name) and isinstance(url, str)
                and isinstance(size, int) and not isinstance(size, bool) and 0 < size <= MAX_DOWNLOAD_BYTES):
            continue
        try:
            check_download_url(url)
        except UpdateError:
            continue
        if urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1] != name:
            continue
        assets.append(Asset(name, url, size))
    page = doc.get("html_url")
    if not (isinstance(page, str) and page.startswith(f"https://github.com/{REPO}/")):
        page = RELEASES_PAGE
    notes = doc.get("body") if isinstance(doc.get("body"), str) else ""
    return Release(version=version, tag=f"v{format_version(version)}", title=str(doc.get("name") or "")[:200],
                   notes=notes[:MAX_NOTES_CHARS], page=page, published=str(doc.get("published_at") or "")[:10],
                   assets=tuple(assets))


def _read_limited(resp, limit: int) -> bytes:
    raw = resp.read(limit + 1)
    if len(raw) > limit:
        raise UpdateError("GitHub sent more data than expected, so it was ignored.")
    return raw


def fetch_latest(opener=None, timeout: float = 10.0) -> Release:
    opener = opener or default_opener()
    req = urllib.request.Request(API_LATEST, headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT,
                                                      "X-GitHub-Api-Version": "2022-11-28"})
    with opener.open(req, timeout=timeout) as resp:
        raw = _read_limited(resp, MAX_API_BYTES)
    try:
        doc = json.loads(raw)
    except ValueError:
        raise UpdateError("GitHub sent an answer the app could not read.") from None
    return parse_release(doc)


def describe_error(exc: BaseException) -> str:
    """A short, plain-language reason for ``exc``."""
    if isinstance(exc, UpdateError):
        return str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 404:
            return "No release has been published yet."
        if exc.code in (403, 429):
            return "GitHub is limiting requests from this network. Try again in a while."
        return f"GitHub answered with an error ({exc.code})."
    if isinstance(exc, urllib.error.URLError):
        if isinstance(exc.reason, ssl.SSLError):
            return ("A secure connection to GitHub could not be made (a certificate problem). "
                    "Check the computer's date and any proxy, or use the download page.")
        return "Can't reach GitHub. Check your internet connection and try again."
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "GitHub did not answer in time. Check your internet connection and try again."
    if isinstance(exc, ssl.SSLError):
        return "A secure connection to GitHub could not be made (a certificate problem)."
    if isinstance(exc, OSError):
        return f"A file error stopped the update: {exc.strerror or exc}"
    return f"Something went wrong: {exc}"


# =============================================================================== this system

_ARCH = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}
_DEB_ARCH = {"x86_64": "amd64", "arm64": "arm64"}


def platform_key(system: Optional[str] = None, machine: Optional[str] = None) -> Optional[str]:
    """``macos-arm64``, ``macos-x86_64``, ``windows-x64``, ``linux-amd64``, ``linux-arm64``; None if there is no installer."""
    system = system or sys.platform
    arch = _ARCH.get((machine or platform.machine()).lower())
    if arch is None:
        return None
    if system == "darwin":
        return f"macos-{arch}"
    if system.startswith("win"):
        return "windows-x64" if arch == "x86_64" else None
    if system.startswith("linux"):
        return f"linux-{_DEB_ARCH[arch]}"
    return None


def asset_name(key: Optional[str], version_text: str) -> Optional[str]:
    """The installer's file name, as ``packaging/build.py`` names it."""
    if not key:
        return None
    if key.startswith("macos-"):
        return f"Echem-Analyzer-{version_text}-{key}.dmg"
    if key == "windows-x64":
        return f"Echem-Analyzer-{version_text}-windows-x64-setup.exe"
    if key.startswith("linux-"):
        return f"echem-analyzer_{version_text}_{key[len('linux-'):]}.deb"
    return None


def app_bundle(executable: Path) -> Optional[Path]:
    """The ``.app`` folder that contains ``executable`` (macOS), if any."""
    return next((p for p in (executable, *executable.parents) if p.suffix == ".app"), None)


def install_kind(frozen: Optional[bool] = None, system: Optional[str] = None, executable: Optional[Path] = None) -> str:
    """``installed`` (set up by an installer: can update itself), ``portable`` (an unzipped folder) or ``source``."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not frozen:
        return "source"
    system = system or sys.platform
    exe = Path(executable or sys.executable)
    if system == "darwin":
        return "installed" if app_bundle(exe) else "portable"
    if system.startswith("win"):
        return "installed" if (exe.parent / "unins000.exe").exists() else "portable"
    return "installed" if Path("/opt/echem-analyzer") in (exe, *exe.parents) else "portable"


def choose_asset(release: Release, key: Optional[str], kind: str) -> Optional[Asset]:
    """The installer this system can apply by itself, or None (then the user is sent to the download page)."""
    if kind != "installed":
        return None
    name = asset_name(key, release.text)
    return release.asset(name) if name else None


def build_info() -> list[tuple[str, str]]:
    """Facts about this installation, for the About window and bug reports."""
    kind = install_kind()
    where = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    mode = {"installed": "Installed", "portable": "Portable", "source": "Running from source"}[kind]
    return [("Version", __version__), ("Installation", mode), ("System", f"{platform.system()} {platform.release()} · {platform.machine()}"),
            ("Python", platform.python_version()), ("Location", str(where))]


# =============================================================================== checksums and download

def parse_sums(text: str) -> dict[str, str]:
    """``SHA256SUMS.txt`` (``<hex>  <name>`` per line, as build.py writes it) as {file name: lower-case hex}."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = re.fullmatch(r"([0-9a-fA-F]{64})[ \t]+\*?(\S.*)", line.strip())
        if m:
            out[Path(m.group(2).strip()).name] = m.group(1).lower()
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _get(url: str, opener, timeout: float, accept: str):
    return opener.open(urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept}), timeout=timeout)


def download_file(url: str, dest: Path, size: int, progress: Progress, cancelled: Callable[[], bool], opener=None,
                  timeout: float = 20.0) -> None:
    """Stream ``url`` into ``dest``; the partial file is removed on cancel or failure. ``size`` is the size GitHub reported."""
    check_download_url(url)
    opener = opener or default_opener()
    got = 0
    try:
        with _get(url, opener, timeout, "application/octet-stream") as resp, open(dest, "wb") as out:
            total = size or int(resp.headers.get("Content-Length") or 0)
            limit = size if size else MAX_DOWNLOAD_BYTES
            while True:
                if cancelled():
                    raise Cancelled()
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                got += len(chunk)
                if got > limit:
                    raise UpdateError("The download is larger than the release says, so it was stopped.")
                out.write(chunk)
                progress(got, total)
        if size and got != size:
            raise UpdateError("The download is incomplete. Check your connection and try again.")
    except BaseException:
        dest.unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class Prepared:
    release: Release
    asset: Asset
    path: Path
    sha256: str


def cleanup_stale(max_age_days: int = STALE_DAYS) -> None:
    """Delete old ``echem-update-*`` download folders from the temp folder (best effort)."""
    cutoff = time.time() - max_age_days * 86400
    for d in Path(tempfile.gettempdir()).glob("echem-update-*"):
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def prepare(release: Release, asset: Asset, progress: Progress, cancelled: Callable[[], bool],
            stage: Callable[[str], None] = lambda _s: None, opener=None, workdir: Optional[Path] = None) -> Prepared:
    """Download ``asset`` into a private temp folder and verify it against the release's checksum list."""
    sums = release.asset(SUMS_NAME)
    if sums is None:
        raise UpdateError("This release has no checksum file, so the download cannot be verified. Use the download page instead.")
    opener = opener or default_opener()
    stage("checksums")
    with _get(sums.url, opener, 20.0, "text/plain") as resp:
        listed = parse_sums(_read_limited(resp, MAX_SUMS_BYTES).decode("utf-8", "replace"))
    expected = listed.get(asset.name)
    if not expected:
        raise UpdateError("The checksum list has no entry for this installer, so it cannot be verified. Use the download page instead.")
    work = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="echem-update-"))
    work.mkdir(parents=True, exist_ok=True)
    dest = work / asset.name
    stage("download")
    download_file(asset.url, dest, asset.size, progress, cancelled, opener)
    stage("verify")
    if sha256_file(dest) != expected:
        dest.unlink(missing_ok=True)
        raise UpdateError("The downloaded file does not match its published checksum, so it was deleted. Try again.")
    return Prepared(release=release, asset=asset, path=dest, sha256=expected)


# =============================================================================== installing

MACOS_SCRIPT = r"""#!/bin/sh
# Echem Analyzer self-update (macOS). Arguments: PID DMG SHA256 TARGET_APP LOG
PID="$1"; DMG="$2"; SUM="$3"; TARGET="$4"; LOG="$5"
exec >>"$LOG" 2>&1
echo "== $(date): update $TARGET from $DMG"
n=0
while kill -0 "$PID" 2>/dev/null; do
  n=$((n+1)); [ "$n" -gt 200 ] && break
  sleep 0.3
done
MNT=""
finish() {   # always leave the user with a working app: the new one, or the old one if anything failed
  if [ -n "$MNT" ]; then hdiutil detach "$MNT" -quiet -force 2>/dev/null; rmdir "$MNT" 2>/dev/null; fi
  open "$TARGET"
  exit "$1"
}
ACTUAL="$(shasum -a 256 "$DMG" | cut -d' ' -f1)"
[ "$ACTUAL" = "$SUM" ] || { echo "checksum mismatch"; finish 1; }
MNT="$(mktemp -d "${TMPDIR:-/tmp}/echem-update-mnt.XXXXXX")" || { MNT=""; finish 1; }
hdiutil attach -nobrowse -readonly -noverify -noautoopen -mountpoint "$MNT" "$DMG" >/dev/null || { echo "attach failed"; rmdir "$MNT"; MNT=""; finish 1; }
SRC="$MNT/Echem Analyzer.app"
[ -d "$SRC" ] || { echo "no app in the disk image"; finish 1; }
NEW="$TARGET.update-new"; OLD="$TARGET.update-old"
rm -rf "$NEW" "$OLD"
ditto "$SRC" "$NEW" || { echo "copy failed"; rm -rf "$NEW"; finish 1; }
mv "$TARGET" "$OLD" || { echo "cannot move the old app aside"; rm -rf "$NEW"; finish 1; }
if mv "$NEW" "$TARGET"; then rm -rf "$OLD"; echo "updated"; else echo "swap failed, restoring"; mv "$OLD" "$TARGET"; rm -rf "$NEW"; finish 1; fi
finish 0
"""

LINUX_SCRIPT = r"""#!/bin/sh
# Echem Analyzer self-update (Debian/Ubuntu). Arguments: PID DEB SHA256 APP LOG
PID="$1"; DEB="$2"; SUM="$3"; APP="$4"; LOG="$5"
exec >>"$LOG" 2>&1
echo "== $(date): install $DEB"
n=0
while kill -0 "$PID" 2>/dev/null; do
  n=$((n+1)); [ "$n" -gt 200 ] && break
  sleep 0.3
done
relaunch() { nohup "$APP" >/dev/null 2>&1 & exit "$1"; }
[ "$(sha256sum "$DEB" | cut -d' ' -f1)" = "$SUM" ] || { echo "checksum mismatch"; relaunch 1; }
if command -v pkexec >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
  # root copies the package into a root-owned folder, then checks and installs that copy: nobody can swap the file in between
  pkexec /bin/sh -c 'set -e; d="$(mktemp -d)"; trap "rm -rf \"$d\"" EXIT; chmod 755 "$d"; cp "$1" "$d/pkg.deb"; chmod 644 "$d/pkg.deb"; [ "$(sha256sum "$d/pkg.deb" | cut -d" " -f1)" = "$2" ]; apt-get install -y "$d/pkg.deb"' sh "$DEB" "$SUM" \
    && echo "installed" || echo "install failed or was cancelled"
elif command -v xdg-open >/dev/null 2>&1; then
  echo "no pkexec: handing the package to the system installer"
  xdg-open "$DEB"
  exit 0
else
  echo "no way to install the package from here"
fi
relaunch 0
"""

WINDOWS_SCRIPT = r"""param([int]$ParentPid, [string]$Installer, [string]$Sha256, [string]$AppExe, [string]$LogPath)
# Echem Analyzer self-update (Windows). Waits for the app to exit, checks the installer, runs it, starts the app again.
function Log($m) { Add-Content -LiteralPath $LogPath -Value ("{0}  {1}" -f (Get-Date -Format s), $m) }
Log "update: $Installer"
try { Wait-Process -Id $ParentPid -Timeout 60 -ErrorAction SilentlyContinue } catch { }
try {
  $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Installer).Hash
  if ($actual -ieq $Sha256) {
    $p = Start-Process -FilePath $Installer -ArgumentList '/SILENT','/SUPPRESSMSGBOXES','/NORESTART' -Wait -PassThru
    Log ("installer exit code {0}" -f $p.ExitCode)
  } else { Log "checksum mismatch: the installer was not run" }
} catch { Log ("failed: {0}" -f $_) }
# the new version if the installer worked, the old one if it did not: the user always gets an app back
try { Start-Process -FilePath $AppExe } catch { Log ("could not start the app: {0}" -f $_) }
"""


@dataclass(frozen=True)
class Plan:
    system: str                 # macos | windows | linux
    argv: list
    script: Path
    log: Path


def _write_script(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    if os.name != "nt":
        path.chmod(0o700)
    return path


def build_plan(prepared: Prepared, system: Optional[str] = None, executable: Optional[Path] = None,
               pid: Optional[int] = None) -> Plan:
    """The helper command that installs ``prepared`` after this process exits. Raises UpdateError if it cannot be done here."""
    system = system or sys.platform
    exe = Path(executable or sys.executable)
    pid = os.getpid() if pid is None else pid
    work = prepared.path.parent
    log = work / "update.log"
    sums = (str(pid), str(prepared.path), prepared.sha256)
    if system == "darwin":
        target = app_bundle(exe)
        if target is None:
            raise UpdateError("This copy of Echem Analyzer is not an app bundle, so it cannot replace itself.")
        if not os.access(target.parent, os.W_OK):
            raise UpdateError(f"The folder {target.parent} is not writable for you, so the app cannot replace itself. "
                              "Open the downloaded disk image yourself and drag the app into Applications.")
        script = _write_script(work / "update-macos.sh", MACOS_SCRIPT)
        return Plan("macos", ["/bin/sh", str(script), *sums, str(target), str(log)], script, log)
    if system.startswith("win"):
        script = _write_script(work / "update-windows.ps1", WINDOWS_SCRIPT)
        ps = shutil.which("powershell.exe") or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        return Plan("windows", [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                                "-File", str(script), *sums, str(exe), str(log)], script, log)
    script = _write_script(work / "update-linux.sh", LINUX_SCRIPT)
    return Plan("linux", ["/bin/sh", str(script), *sums, str(exe), str(log)], script, log)


def launch(plan: Plan) -> subprocess.Popen:
    """Start the helper detached from this process, so it outlives the app."""
    kw: dict = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    return subprocess.Popen(plan.argv, **kw)
