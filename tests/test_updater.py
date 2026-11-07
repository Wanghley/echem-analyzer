"""The update engine (no Qt): version rules, release validation, safe downloads, checksums and the install helpers.

Nothing here touches the network: a fake opener serves the bytes.  The macOS and Linux helper scripts are run for real
(with stand-ins for ``open``, ``pkexec`` and ``apt-get``), so what ships is what is tested; the Windows script cannot run
on this kind of machine and is checked for its shape only.
"""
import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from echem_analyzer import timefmt, updater
from echem_analyzer.updater import Asset, Cancelled, UpdateError

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("echem_build_for_updater", ROOT / "packaging" / "build.py")
build = importlib.util.module_from_spec(_spec)
sys.modules["echem_build_for_updater"] = build
_spec.loader.exec_module(build)

BASE = "https://github.com/Wanghley/echem-analyzer/releases/download"
ALL_KEYS = ["macos-arm64", "macos-x86_64", "windows-x64", "linux-amd64", "linux-arm64"]


# ===================================================================== fakes
class FakeResp:
    def __init__(self, data: bytes, headers=None):
        self._b = io.BytesIO(data)
        self.headers = headers or {"Content-Length": str(len(data))}

    def read(self, n=-1):
        return self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """Serves ``routes`` (url -> bytes, or an exception to raise) and records every request."""

    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def open(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        self.requests.append((url, dict(getattr(req, "headers", {})), timeout))
        item = self.routes[url]
        if isinstance(item, BaseException):
            raise item
        return FakeResp(item)


def asset_doc(name, size=1234, tag="v0.6.0"):
    return {"name": name, "size": size, "browser_download_url": f"{BASE}/{tag}/{name}"}


def release_doc(version="0.6.0", extra=(), body="## What's new\n- things"):
    names = [updater.asset_name(k, version) for k in ALL_KEYS] + [updater.SUMS_NAME]
    return {"tag_name": f"v{version}", "name": f"Echem Analyzer v{version}", "body": body, "draft": False, "prerelease": False,
            "html_url": f"https://github.com/Wanghley/echem-analyzer/releases/tag/v{version}", "published_at": "2026-11-20T10:00:00Z",
            "assets": [asset_doc(n, tag=f"v{version}") for n in names] + list(extra)}


def make_release(version="0.6.0", **kw):
    return updater.parse_release(release_doc(version, **kw))


# ===================================================================== versions
@pytest.mark.parametrize("text,expected", [("0.6.0", (0, 6, 0)), ("v1.2.3", (1, 2, 3)), (" v10.0.12 ", (10, 0, 12)),
                                           ("0.6", None), ("0.6.0-rc1", None), ("v0.6.0.1", None), ("", None), ("latest", None),
                                           ("1.2.x", None), (None, None)])
def test_parse_version(text, expected):
    assert updater.parse_version(text) == expected


def test_versions_compare_as_numbers_not_text():
    assert updater.is_newer((0, 10, 0), (0, 9, 0)) and updater.is_newer((1, 0, 0), (0, 99, 99))
    assert not updater.is_newer((0, 5, 0), (0, 5, 0)) and not updater.is_newer((0, 4, 9), (0, 5, 0))
    assert updater.format_version((0, 10, 2)) == "0.10.2"


# ===================================================================== release validation
def test_a_good_release_is_parsed():
    r = make_release()
    assert r.version == (0, 6, 0) and r.tag == "v0.6.0" and r.published == "2026-11-20"
    assert r.page.endswith("/releases/tag/v0.6.0") and "What's new" in r.notes
    assert r.asset(updater.SUMS_NAME) and len(r.assets) == len(ALL_KEYS) + 1


@pytest.mark.parametrize("patch", [{"draft": True}, {"prerelease": True}, {"tag_name": "nightly"}, {"tag_name": "v0.6.0-rc1"}])
def test_unusable_releases_are_refused(patch):
    doc = {**release_doc(), **patch}
    with pytest.raises(UpdateError):
        updater.parse_release(doc)


def test_junk_is_refused():
    for doc in (None, [], "x", 5):
        with pytest.raises(UpdateError):
            updater.parse_release(doc)


@pytest.mark.parametrize("bad", [
    {"name": "x.dmg", "size": 10, "browser_download_url": "http://github.com/Wanghley/echem-analyzer/releases/download/v0.6.0/x.dmg"},
    {"name": "x.dmg", "size": 10, "browser_download_url": "https://evil.example/Wanghley/echem-analyzer/releases/download/v0.6.0/x.dmg"},
    {"name": "x.dmg", "size": 10, "browser_download_url": "https://github.com.evil.example/Wanghley/echem-analyzer/releases/download/v0.6.0/x.dmg"},
    {"name": "x.dmg", "size": 10, "browser_download_url": "https://github.com/other/repo/releases/download/v0.6.0/x.dmg"},
    {"name": "x.dmg", "size": 10, "browser_download_url": "https://github.com/Wanghley/echem-analyzer/releases/download/../../x.dmg"},
    {"name": "x.dmg", "size": 10, "browser_download_url": "https://user:pw@github.com/Wanghley/echem-analyzer/releases/download/v0.6.0/x.dmg"},
    {"name": "x.dmg", "size": 10, "browser_download_url": "https://github.com:8443/Wanghley/echem-analyzer/releases/download/v0.6.0/x.dmg"},
    {"name": "x.dmg", "size": 10, "browser_download_url": "https://github.com/Wanghley/echem-analyzer/releases/download/v0.6.0/x.dmg?token=1"},
    {"name": "../../etc/passwd", "size": 10, "browser_download_url": f"{BASE}/v0.6.0/../../etc/passwd"},
    {"name": "a b.dmg", "size": 10, "browser_download_url": f"{BASE}/v0.6.0/a b.dmg"},
    {"name": "x.dmg", "size": 10, "browser_download_url": f"{BASE}/v0.6.0/other-name.dmg"},       # URL must end in the asset's name
    {"name": "x.dmg", "size": -1, "browser_download_url": f"{BASE}/v0.6.0/x.dmg"},
    {"name": "x.dmg", "size": 0, "browser_download_url": f"{BASE}/v0.6.0/x.dmg"},
    {"name": "x.dmg", "size": 10**12, "browser_download_url": f"{BASE}/v0.6.0/x.dmg"},
    {"name": "x.dmg", "size": True, "browser_download_url": f"{BASE}/v0.6.0/x.dmg"},
    {"name": "x.dmg", "size": "10", "browser_download_url": f"{BASE}/v0.6.0/x.dmg"},
    {"name": 5, "size": 10, "browser_download_url": f"{BASE}/v0.6.0/x.dmg"},
    "not a dict", None,
])
def test_a_suspicious_asset_is_dropped_and_never_trusted(bad):
    r = updater.parse_release({**release_doc(), "assets": [bad]})
    assert r.assets == ()


def test_notes_are_capped_and_an_odd_page_link_falls_back():
    r = updater.parse_release({**release_doc(), "body": "x" * 100_000, "html_url": "https://evil.example/x"})
    assert len(r.notes) == updater.MAX_NOTES_CHARS and r.page == updater.RELEASES_PAGE
    assert updater.parse_release({**release_doc(), "body": None}).notes == ""


# ===================================================================== fetching
def test_fetch_latest_asks_github_politely_and_parses(monkeypatch):
    op = FakeOpener({updater.API_LATEST: json.dumps(release_doc()).encode()})
    r = updater.fetch_latest(opener=op, timeout=7)
    url, headers, timeout = op.requests[0]
    assert url == updater.API_LATEST and url.startswith("https://") and timeout == 7
    assert headers["User-agent"].startswith("echem-analyzer/") and "github" in headers["Accept"]
    assert r.version == (0, 6, 0)


def test_fetch_latest_refuses_huge_or_unreadable_answers():
    with pytest.raises(UpdateError):
        updater.fetch_latest(opener=FakeOpener({updater.API_LATEST: b"x" * (updater.MAX_API_BYTES + 5)}))
    with pytest.raises(UpdateError):
        updater.fetch_latest(opener=FakeOpener({updater.API_LATEST: b"<html>not json</html>"}))


@pytest.mark.parametrize("exc,words", [
    (urllib.error.HTTPError(updater.API_LATEST, 404, "nf", {}, None), "No release"),
    (urllib.error.HTTPError(updater.API_LATEST, 403, "rl", {}, None), "limiting"),
    (urllib.error.HTTPError(updater.API_LATEST, 500, "x", {}, None), "500"),
    (urllib.error.URLError("no route"), "Can't reach GitHub"),
    (TimeoutError("slow"), "did not answer"),
    (PermissionError(13, "Permission denied"), "Permission denied"),
    (UpdateError("custom text"), "custom text"),
    (RuntimeError("boom"), "boom"),
])
def test_errors_are_explained_in_plain_language(exc, words):
    assert words in updater.describe_error(exc)


def test_a_certificate_problem_gets_its_own_explanation():
    import ssl
    msg = updater.describe_error(urllib.error.URLError(ssl.SSLCertVerificationError("bad cert")))
    assert "certificate" in msg and "download page" in msg


# ===================================================================== redirects and urls
@pytest.mark.parametrize("url,ok", [
    ("https://github.com/x", True), ("https://objects.githubusercontent.com/abc", True),
    ("https://release-assets.githubusercontent.com/abc", True), ("http://github.com/x", False),
    ("https://githubusercontent.com.evil.example/x", False), ("https://evilgithubusercontent.com/x", False),
    ("https://evil.example/github.com", False), ("https://github.com@evil.example/x", False),
    ("https://user:pw@github.com/x", False), ("https://github.com:444/x", False), ("ftp://github.com/x", False),
])
def test_redirects_may_only_go_to_github_hosts_over_https(url, ok):
    assert updater.redirect_allowed(url) is ok


def test_the_redirect_handler_refuses_a_foreign_target():
    import urllib.request
    h = updater._SafeRedirect()
    req = urllib.request.Request(f"{BASE}/v0.6.0/x.dmg")
    with pytest.raises(UpdateError):
        h.redirect_request(req, None, 302, "Found", {}, "https://evil.example/x.dmg")
    assert h.redirect_request(req, None, 302, "Found", {}, "https://objects.githubusercontent.com/x.dmg") is not None


# ===================================================================== this system
@pytest.mark.parametrize("system,machine,key", [
    ("darwin", "arm64", "macos-arm64"), ("darwin", "x86_64", "macos-x86_64"), ("win32", "AMD64", "windows-x64"),
    ("win32", "ARM64", None), ("linux", "x86_64", "linux-amd64"), ("linux", "aarch64", "linux-arm64"),
    ("linux", "riscv64", None), ("freebsd14", "amd64", None)])
def test_platform_key(system, machine, key):
    assert updater.platform_key(system, machine) == key


def test_asset_names_are_exactly_what_the_build_script_produces():
    """If packaging/build.py renames an installer, self-update would silently stop finding it: this test is the tripwire."""
    for key, target, arch in (("macos-arm64", "macos", "arm64"), ("macos-x86_64", "macos", "x86_64"), ("windows-x64", "windows", "x86_64"),
                              ("linux-amd64", "linux", "x86_64"), ("linux-arm64", "linux", "arm64")):
        names = build.artifact_names(target, "1.2.3", arch)
        assert updater.asset_name(key, "1.2.3") in names, (key, names)
    assert updater.asset_name("windows-x64", "1.2.3") == build.artifact_names("windows", "1.2.3")[0]      # the installer, not the zip
    assert updater.SUMS_NAME == "SHA256SUMS.txt"


def test_install_kind(tmp_path):
    assert updater.install_kind(frozen=False) == "source"
    app = tmp_path / "Applications" / "Echem Analyzer.app" / "Contents" / "MacOS" / "EchemAnalyzer"
    assert updater.install_kind(True, "darwin", app) == "installed"
    assert updater.install_kind(True, "darwin", tmp_path / "EchemAnalyzer") == "portable"
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / "EchemAnalyzer.exe").write_text("x")
    assert updater.install_kind(True, "win32", inst / "EchemAnalyzer.exe") == "portable"
    (inst / "unins000.exe").write_text("x")
    assert updater.install_kind(True, "win32", inst / "EchemAnalyzer.exe") == "installed"
    assert updater.install_kind(True, "linux", Path("/opt/echem-analyzer/echem-analyzer")) == "installed"
    assert updater.install_kind(True, "linux", tmp_path / "echem-analyzer") == "portable"


def test_only_installed_copies_get_an_installer_to_apply():
    r = make_release()
    a = updater.choose_asset(r, "macos-arm64", "installed")
    assert a and a.name == "Echem-Analyzer-0.6.0-macos-arm64.dmg"
    assert updater.choose_asset(r, "macos-arm64", "portable") is None and updater.choose_asset(r, "macos-arm64", "source") is None
    assert updater.choose_asset(r, None, "installed") is None
    assert updater.choose_asset(updater.parse_release({**release_doc(), "assets": []}), "macos-arm64", "installed") is None
    assert updater.choose_asset(r, "windows-x64", "installed").name.endswith("windows-x64-setup.exe")      # never the portable zip


def test_build_info_describes_this_installation():
    info = dict(updater.build_info())
    assert info["Version"] == updater.__version__ and info["Installation"] and info["Python"] and info["Location"]


# ===================================================================== checksums
def test_sums_parse_exactly_what_the_build_script_writes(tmp_path):
    f1, f2 = tmp_path / "a-1.dmg", tmp_path / "b 2.exe"
    f1.write_bytes(b"alpha"), f2.write_bytes(b"beta")
    build.write_checksum(f1)
    build.write_checksum(f2)
    text = (tmp_path / "a-1.dmg.sha256").read_text() + (tmp_path / "b 2.exe.sha256").read_text()
    sums = updater.parse_sums(text)
    assert sums == {"a-1.dmg": hashlib.sha256(b"alpha").hexdigest(), "b 2.exe": hashlib.sha256(b"beta").hexdigest()}
    assert updater.sha256_file(f1) == sums["a-1.dmg"]


def test_sums_ignore_malformed_lines_and_tolerate_the_binary_marker():
    h = "a" * 64
    sums = updater.parse_sums(f"junk\n{h[:-1]}  short.bin\n{h.upper()} *star.bin\n{h}  dir/inner.bin\n\n")
    assert sums == {"star.bin": h, "inner.bin": h}


# ===================================================================== download and prepare
PAYLOAD = os.urandom(700_000)


def sums_for(name, data=PAYLOAD):
    return f"{hashlib.sha256(data).hexdigest()}  {name}\n".encode()


def routes_for(release, data=PAYLOAD, sums=None):
    asset = updater.choose_asset(release, "macos-arm64", "installed")
    return asset, {asset.url: data, release.asset(updater.SUMS_NAME).url: sums if sums is not None else sums_for(asset.name, data)}


def test_prepare_downloads_verifies_and_reports_progress(tmp_path):
    r = make_release()
    asset = updater.choose_asset(r, "macos-arm64", "installed")
    asset = Asset(asset.name, asset.url, len(PAYLOAD))
    op = FakeOpener({asset.url: PAYLOAD, r.asset(updater.SUMS_NAME).url: sums_for(asset.name)})
    seen, stages = [], []
    prep = updater.prepare(r, asset, lambda d, t: seen.append((d, t)), lambda: False, stages.append, opener=op, workdir=tmp_path / "w")
    assert prep.path.read_bytes() == PAYLOAD and prep.sha256 == hashlib.sha256(PAYLOAD).hexdigest()
    assert stages == ["checksums", "download", "verify"]
    assert seen[-1] == (len(PAYLOAD), len(PAYLOAD)) and [d for d, _ in seen] == sorted(d for d, _ in seen) and len(seen) > 1
    assert all(url.startswith("https://") for url, *_ in op.requests)


def test_a_wrong_checksum_deletes_the_file_and_refuses(tmp_path):
    r = make_release()
    asset = updater.choose_asset(r, "macos-arm64", "installed")
    asset = Asset(asset.name, asset.url, len(PAYLOAD))
    op = FakeOpener({asset.url: PAYLOAD, r.asset(updater.SUMS_NAME).url: sums_for(asset.name, b"something else")})
    with pytest.raises(UpdateError, match="checksum"):
        updater.prepare(r, asset, lambda d, t: None, lambda: False, opener=op, workdir=tmp_path)
    assert not (tmp_path / asset.name).exists()


def test_no_checksum_list_or_no_entry_means_no_install(tmp_path):
    r = make_release()
    asset = updater.choose_asset(r, "macos-arm64", "installed")
    no_sums = updater.parse_release({**release_doc(), "assets": [asset_doc(asset.name)]})
    with pytest.raises(UpdateError, match="no checksum file"):
        updater.prepare(no_sums, asset, lambda d, t: None, lambda: False, opener=FakeOpener({}), workdir=tmp_path)
    op = FakeOpener({r.asset(updater.SUMS_NAME).url: sums_for("some-other-file.dmg")})
    with pytest.raises(UpdateError, match="no entry"):
        updater.prepare(r, asset, lambda d, t: None, lambda: False, opener=op, workdir=tmp_path)
    assert not list(tmp_path.iterdir())                                       # nothing was downloaded without a way to verify it


def test_cancelling_stops_the_download_and_removes_the_partial_file(tmp_path):
    r = make_release()
    asset = Asset("x.dmg", f"{BASE}/v0.6.0/x.dmg", len(PAYLOAD))
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return calls["n"] > 2
    with pytest.raises(Cancelled):
        updater.download_file(asset.url, tmp_path / "x.dmg", asset.size, lambda d, t: None, cancelled, opener=FakeOpener({asset.url: PAYLOAD}))
    assert not (tmp_path / "x.dmg").exists()


def test_a_short_or_oversized_download_is_refused(tmp_path):
    url = f"{BASE}/v0.6.0/x.dmg"
    with pytest.raises(UpdateError, match="incomplete"):
        updater.download_file(url, tmp_path / "a", len(PAYLOAD) + 10, lambda d, t: None, lambda: False, opener=FakeOpener({url: PAYLOAD}))
    with pytest.raises(UpdateError, match="larger"):
        updater.download_file(url, tmp_path / "b", len(PAYLOAD) - 10, lambda d, t: None, lambda: False, opener=FakeOpener({url: PAYLOAD}))
    assert not (tmp_path / "a").exists() and not (tmp_path / "b").exists()


def test_a_network_failure_mid_download_leaves_nothing_behind(tmp_path):
    url = f"{BASE}/v0.6.0/x.dmg"
    with pytest.raises(urllib.error.URLError):
        updater.download_file(url, tmp_path / "x", 10, lambda d, t: None, lambda: False, opener=FakeOpener({url: urllib.error.URLError("down")}))
    assert not (tmp_path / "x").exists()


def test_downloads_only_from_this_projects_github_releases(tmp_path):
    for bad in ("http://github.com/Wanghley/echem-analyzer/releases/download/v1/x", "https://evil.example/x",
                "https://github.com/someone/else/releases/download/v1/x"):
        with pytest.raises(UpdateError):
            updater.download_file(bad, tmp_path / "x", 10, lambda d, t: None, lambda: False, opener=FakeOpener({}))


def test_old_download_folders_are_cleaned_up_but_young_ones_and_strangers_are_not(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    old, young, other = tmp_path / "echem-update-old", tmp_path / "echem-update-new", tmp_path / "unrelated"
    for d in (old, young, other):
        d.mkdir()
        (d / "f").write_text("x")
    t = old.stat().st_mtime - 10 * 86400
    os.utime(old, (t, t))
    os.utime(other, (t, t))
    updater.cleanup_stale()
    assert not old.exists() and young.exists() and other.exists()


# ===================================================================== install plans
def prepared_in(tmp_path, name="Echem-Analyzer-0.6.0-macos-arm64.dmg", data=b"image"):
    p = tmp_path / name
    p.write_bytes(data)
    r = make_release()
    return updater.Prepared(r, Asset(name, f"{BASE}/v0.6.0/{name}", len(data)), p, hashlib.sha256(data).hexdigest())


def test_macos_plan_replaces_the_bundle_it_runs_from(tmp_path):
    apps = tmp_path / "Applications"
    exe = apps / "Echem Analyzer.app" / "Contents" / "MacOS" / "EchemAnalyzer"
    exe.parent.mkdir(parents=True)
    exe.write_text("x")
    prep = prepared_in(tmp_path)
    plan = updater.build_plan(prep, "darwin", exe, pid=4242)
    assert plan.system == "macos" and plan.argv[0] == "/bin/sh"
    assert plan.argv[2:] == ["4242", str(prep.path), prep.sha256, str(apps / "Echem Analyzer.app"), str(plan.log)]
    assert plan.script.read_text() == updater.MACOS_SCRIPT and os.access(plan.script, os.X_OK)
    for secret in (str(tmp_path), prep.sha256):
        assert secret not in updater.MACOS_SCRIPT                              # arguments, never spliced into the script


def test_macos_plan_refuses_a_folder_it_cannot_write_to_and_a_non_bundle(tmp_path):
    exe = tmp_path / "ro" / "Echem Analyzer.app" / "Contents" / "MacOS" / "EchemAnalyzer"
    exe.parent.mkdir(parents=True)
    prep = prepared_in(tmp_path)
    with pytest.raises(UpdateError, match="not an app bundle"):
        updater.build_plan(prep, "darwin", tmp_path / "EchemAnalyzer")
    if os.geteuid() != 0:
        (tmp_path / "ro").chmod(0o500)
        try:
            with pytest.raises(UpdateError, match="not writable"):
                updater.build_plan(prep, "darwin", exe)
        finally:
            (tmp_path / "ro").chmod(0o700)


def test_windows_plan_passes_everything_as_arguments(tmp_path):
    prep = prepared_in(tmp_path, "Echem-Analyzer-0.6.0-windows-x64-setup.exe")
    exe = Path("C:/Users/Ada Lovelace/AppData/Local/Programs/Echem Analyzer/EchemAnalyzer.exe")
    plan = updater.build_plan(prep, "win32", exe, pid=77)
    assert plan.system == "windows" and "-File" in plan.argv and "Bypass" in plan.argv
    assert plan.argv[plan.argv.index("-File") + 1:][1:5] == ["77", str(prep.path), prep.sha256, str(exe)]
    text = plan.script.read_text()
    for needed in ("Get-FileHash", "-ieq $Sha256", "'/SILENT'", "'/NORESTART'", "Start-Process -FilePath $AppExe", "Wait-Process"):
        assert needed in text, needed
    assert str(tmp_path) not in text and prep.sha256 not in text


def test_linux_plan(tmp_path):
    prep = prepared_in(tmp_path, "echem-analyzer_0.6.0_amd64.deb")
    plan = updater.build_plan(prep, "linux", Path("/opt/echem-analyzer/echem-analyzer"), pid=9)
    assert plan.system == "linux" and plan.argv[:2] == ["/bin/sh", str(plan.script)]
    assert plan.argv[2:] == ["9", str(prep.path), prep.sha256, "/opt/echem-analyzer/echem-analyzer", str(plan.log)]
    assert "pkexec" in updater.LINUX_SCRIPT and "cp \"$1\"" in updater.LINUX_SCRIPT       # the root-owned copy that closes the swap window


def test_launch_detaches_the_helper(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(updater.subprocess, "Popen", lambda argv, **kw: seen.update(argv=argv, kw=kw) or "proc")
    plan = updater.Plan("linux", ["/bin/sh", "x"], tmp_path / "x", tmp_path / "log")
    assert updater.launch(plan) == "proc" and seen["argv"] == ["/bin/sh", "x"]
    assert seen["kw"]["start_new_session"] is True and seen["kw"]["close_fds"] is True
    assert seen["kw"]["stdin"] == subprocess.DEVNULL


# ===================================================================== the scripts, run for real
def stub(dirpath: Path, name: str, body: str) -> None:
    f = dirpath / name
    f.write_text("#!/bin/sh\n" + body)
    f.chmod(f.stat().st_mode | stat.S_IEXEC)


def run_script(plan_argv, bindir, extra_env=None, timeout=60):
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", **(extra_env or {})}
    return subprocess.run(plan_argv, env=env, capture_output=True, text=True, timeout=timeout)


def dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


@pytest.mark.skipif(sys.platform != "darwin", reason="hdiutil and ditto exist only on macOS")
class TestMacosScript:
    @staticmethod
    def make_dmg(tmp_path, version_text):
        src = tmp_path / "src"
        app = src / "Echem Analyzer.app" / "Contents" / "MacOS"
        app.mkdir(parents=True)
        (app / "EchemAnalyzer").write_text(version_text)
        (src / "Echem Analyzer.app" / "Contents" / "Info.txt").write_text(version_text)
        dmg = tmp_path / "Echem-Analyzer-0.6.0-macos-arm64.dmg"
        subprocess.run(["hdiutil", "create", "-volname", "t", "-srcfolder", str(src), "-ov", "-format", "UDZO", str(dmg)],
                       check=True, capture_output=True)
        return dmg

    def setup_case(self, tmp_path):
        dmg = self.make_dmg(tmp_path, "NEW 0.6.0")
        target = tmp_path / "Applications" / "Echem Analyzer.app"
        (target / "Contents" / "MacOS").mkdir(parents=True)
        (target / "Contents" / "MacOS" / "EchemAnalyzer").write_text("OLD 0.5.0")
        bindir = tmp_path / "bin"
        bindir.mkdir()
        stub(bindir, "open", f'echo "$@" >> "{tmp_path}/open.log"\n')
        prep = updater.Prepared(make_release(), Asset(dmg.name, "x", dmg.stat().st_size), dmg, updater.sha256_file(dmg))
        exe = target / "Contents" / "MacOS" / "EchemAnalyzer"
        return prep, target, bindir, exe

    def test_the_new_app_replaces_the_old_one_and_is_opened(self, tmp_path):
        prep, target, bindir, exe = self.setup_case(tmp_path)
        plan = updater.build_plan(prep, "darwin", exe, pid=dead_pid())
        r = run_script(plan.argv, bindir)
        assert r.returncode == 0, r.stderr + plan.log.read_text()
        assert (target / "Contents" / "MacOS" / "EchemAnalyzer").read_text() == "NEW 0.6.0"
        assert not Path(str(target) + ".update-old").exists() and not Path(str(target) + ".update-new").exists()
        assert (tmp_path / "open.log").read_text().strip() == str(target)
        assert "updated" in plan.log.read_text()
        assert str(prep.path) not in subprocess.run(["hdiutil", "info"], capture_output=True, text=True).stdout

    def test_a_tampered_image_is_never_installed_and_the_old_app_is_reopened(self, tmp_path):
        prep, target, bindir, exe = self.setup_case(tmp_path)
        plan = updater.build_plan(prep, "darwin", exe, pid=dead_pid())
        prep.path.write_bytes(prep.path.read_bytes() + b"tampered")
        r = run_script(plan.argv, bindir)
        assert r.returncode == 1
        assert (target / "Contents" / "MacOS" / "EchemAnalyzer").read_text() == "OLD 0.5.0"
        assert (tmp_path / "open.log").read_text().strip() == str(target)                          # the user still gets an app
        assert "checksum mismatch" in plan.log.read_text()

    def test_an_image_without_the_app_leaves_the_old_one_in_place(self, tmp_path):
        prep, target, bindir, exe = self.setup_case(tmp_path)
        junk = tmp_path / "junk"
        junk.mkdir()
        (junk / "readme.txt").write_text("no app here")
        empty = tmp_path / "empty.dmg"
        subprocess.run(["hdiutil", "create", "-volname", "t", "-srcfolder", str(junk), "-ov", "-format", "UDZO", str(empty)], check=True, capture_output=True)
        prep = updater.Prepared(prep.release, Asset(empty.name, "x", 1), empty, updater.sha256_file(empty))
        plan = updater.build_plan(prep, "darwin", exe, pid=dead_pid())
        r = run_script(plan.argv, bindir)
        assert r.returncode == 1 and (target / "Contents" / "MacOS" / "EchemAnalyzer").read_text() == "OLD 0.5.0"
        assert "no app in the disk image" in plan.log.read_text()

    def test_it_waits_for_the_app_to_quit_first(self, tmp_path):
        import threading
        import time
        prep, target, bindir, exe = self.setup_case(tmp_path)
        sleeper = subprocess.Popen(["sleep", "2"])
        threading.Thread(target=sleeper.wait, daemon=True).start()           # reap it when it exits, as a real parent would
        plan = updater.build_plan(prep, "darwin", exe, pid=sleeper.pid)
        t0 = time.monotonic()
        r = run_script(plan.argv, bindir)
        assert r.returncode == 0 and time.monotonic() - t0 >= 1.5               # it did not touch the app while it was still running
        assert (target / "Contents" / "MacOS" / "EchemAnalyzer").read_text() == "NEW 0.6.0"


def log_after_restart(log: Path, timeout: float = 5.0) -> str:
    """The stand-in app's log once it has been started (the script starts it in the background), or what there is at the deadline."""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log.exists() and "started" in log.read_text():
            return log.read_text()
        time.sleep(0.05)
    return log.read_text() if log.exists() else ""


@pytest.mark.skipif(os.name == "nt", reason="needs a POSIX shell")
class TestLinuxScript:
    def setup_case(self, tmp_path, data=b"deb-bytes"):
        deb = tmp_path / "echem-analyzer_0.6.0_amd64.deb"
        deb.write_bytes(data)
        bindir = tmp_path / "bin"
        bindir.mkdir()
        log = tmp_path / "calls.log"
        # stand-ins: pkexec just runs the command (as this user), apt-get records what it was asked to install
        stub(bindir, "pkexec", 'exec "$@"\n')
        stub(bindir, "apt-get", f'echo "apt-get $@" >> "{log}"\nfor last; do :; done\n'
                                f'echo "present=$(test -f "$last" && echo yes || echo no) bytes=$(wc -c < "$last" | tr -d " ")" >> "{log}"\n')
        stub(bindir, "sha256sum", 'if [ -x /usr/bin/sha256sum ]; then exec /usr/bin/sha256sum "$@"; else exec shasum -a 256 "$@"; fi\n')
        app = tmp_path / "app"
        stub(tmp_path, "app", f'echo started >> "{log}"\n')
        prep = updater.Prepared(make_release(), Asset(deb.name, "x", len(data)), deb, hashlib.sha256(data).hexdigest())
        return prep, bindir, log, app

    def test_installs_through_pkexec_from_a_verified_root_owned_copy(self, tmp_path):
        prep, bindir, log, app = self.setup_case(tmp_path)
        plan = updater.build_plan(prep, "linux", app, pid=dead_pid())
        r = run_script(plan.argv, bindir)
        calls = log_after_restart(log)
        assert r.returncode == 0, r.stderr + plan.log.read_text()
        assert "apt-get install -y " in calls and "pkg.deb" in calls and str(prep.path) not in calls.split("apt-get")[1].split("\n")[0]
        assert "present=yes bytes=9" in calls and "started" in calls
        assert "installed" in plan.log.read_text()

    def test_a_tampered_package_is_not_installed_and_the_old_app_is_restarted(self, tmp_path):
        prep, bindir, log, app = self.setup_case(tmp_path)
        plan = updater.build_plan(prep, "linux", app, pid=dead_pid())
        prep.path.write_bytes(b"tampered")
        r = run_script(plan.argv, bindir)
        calls = log_after_restart(log)
        assert r.returncode == 1 and "apt-get" not in calls and "started" in calls
        assert "checksum mismatch" in plan.log.read_text()

    def test_a_swap_after_the_first_check_is_caught_by_the_privileged_check(self, tmp_path):
        """The window between the user-level check and root's copy: if the file changed, root's own check must refuse."""
        prep, bindir, log, app = self.setup_case(tmp_path)
        # pkexec stand-in that swaps the file just before running the privileged command, as an attacker would
        stub(bindir, "pkexec", f'printf evil > "{prep.path}"\nexec "$@"\n')
        plan = updater.build_plan(prep, "linux", app, pid=dead_pid())
        run_script(plan.argv, bindir)
        calls = log_after_restart(log)
        assert "apt-get" not in calls and "started" in calls                    # nothing installed; the old app is back
        assert "install failed" in plan.log.read_text()

    def test_without_pkexec_the_package_is_handed_to_the_system_installer(self, tmp_path):
        prep, bindir, log, app = self.setup_case(tmp_path)
        (bindir / "pkexec").unlink()
        stub(bindir, "xdg-open", f'echo "xdg-open $@" >> "{log}"\n')
        plan = updater.build_plan(prep, "linux", app, pid=dead_pid())
        # A PATH that cannot contain pkexec: only our stubs and the few utilities the script uses (a real system, such as a CI runner,
        # may well have /usr/bin/pkexec, and then the script would rightly prefer it).
        tools = tmp_path / "tools"
        tools.mkdir()
        for name in ("date", "cut", "sleep", "nohup", "shasum"):
            found = shutil.which(name)
            if found:
                (tools / name).symlink_to(found)
        env_path = f"{bindir}:{tools}"
        assert shutil.which("pkexec", path=env_path) is None
        r = subprocess.run(plan.argv, env={**os.environ, "PATH": env_path}, capture_output=True, text=True, timeout=30)
        assert r.returncode == 0 and f"xdg-open {prep.path}" in log.read_text()


# ===================================================================== time helpers
def test_humanize_age():
    now = 1_800_000_000.0
    cases = [(0, "just now"), (30, "just now"), (60, "1 minute ago"), (5 * 60, "5 minutes ago"), (3600, "1 hour ago"),
             (5 * 3600, "5 hours ago"), (30 * 3600, "yesterday"), (3 * 86400, "3 days ago"), (14 * 86400, "2 weeks ago")]
    for delta, text in cases:
        assert timefmt.humanize_age(now - delta, now) == text, delta
    assert timefmt.humanize_age(now + 500, now) == "just now"                     # a clock a little ahead
    assert timefmt.humanize_age(now - 400 * 86400, now).split()[-1].isdigit()     # an old file shows its date


def test_human_bytes():
    assert timefmt.human_bytes(512) == "512 B" and timefmt.human_bytes(12_300_000) == "12.3 MB" and timefmt.human_bytes(2_500_000_000) == "2.5 GB"


# ===================================================================== the real v0.5.0 release
FIXTURES = ROOT / "tests" / "fixtures"


def test_the_real_v0_5_0_release_is_understood_end_to_end():
    """The release GitHub actually serves (fetched once with the owner's credentials and trimmed to the fields the app reads)."""
    r = updater.parse_release(json.loads((FIXTURES / "release_v0_5_0.json").read_text(encoding="utf-8")))
    sums = updater.parse_sums((FIXTURES / "SHA256SUMS_v0_5_0.txt").read_text(encoding="utf-8"))
    assert r.version == (0, 5, 0) and len(r.assets) == 6 and r.asset(updater.SUMS_NAME)
    for key in ("macos-arm64", "macos-x86_64", "windows-x64", "linux-amd64"):
        asset = updater.choose_asset(r, key, "installed")
        assert asset is not None, key
        assert sums.get(asset.name) and len(sums[asset.name]) == 64, f"{key}: the installer has no checksum entry"
    assert updater.choose_asset(r, "linux-arm64", "installed") is None          # CI builds no arm64 package: the download page is the way
    assert not updater.is_newer(r.version, (0, 5, 0)) and updater.is_newer(r.version, (0, 4, 0))
