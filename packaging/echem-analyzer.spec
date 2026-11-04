# PyInstaller spec for Echem Analyzer. Build with:  uv run python packaging/build.py   (or: pyinstaller packaging/echem-analyzer.spec)
# One folder ("onedir"): fast start-up and what every installer below wraps. macOS additionally gets a proper .app bundle.
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "echem_analyzer" / "__init__.py").read_text(encoding="utf-8")).group(1)
APP_NAME = "Echem Analyzer"
BUNDLE_ID = "com.wanghley.echem-analyzer"
EXE_NAME = "echem-analyzer" if sys.platform.startswith("linux") else "EchemAnalyzer"
ICON = {"darwin": "icon.icns", "win32": "icon.ico"}.get(sys.platform)

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    datas=[],
    # every module of the package, including ones only imported inside functions
    hiddenimports=collect_submodules("echem_analyzer"),
    # big things the app never uses (PyInstaller would otherwise follow some of them through optional imports)
    excludes=["tkinter", "IPython", "pytest", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
              "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtBluetooth"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=EXE_NAME,
    console=False,                      # a windowed program: no terminal window on Windows or macOS
    icon=str(ROOT / "assets" / ICON) if ICON else None,
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=EXE_NAME)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=str(ROOT / "assets" / "icon.icns"),
        bundle_identifier=BUNDLE_ID,
        version=VERSION,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.education",
            # double-clicking a .echemproj opens it here
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "Echem Analyzer project",
                "CFBundleTypeExtensions": ["echemproj"],
                "CFBundleTypeRole": "Editor",
                "LSHandlerRank": "Owner",
                "CFBundleTypeIconFile": "icon.icns",
            }],
        },
    )
