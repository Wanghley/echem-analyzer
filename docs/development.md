# Development

Running from source, the tests, versioning and releases, and building the installers.

## Launch from the source

Double-click **`run.command`**, or from a terminal:

```sh
./run.command                              # empty window
./run.command "<project, files or folder>"  # open straight away; a .echemproj opens as a project
```

The first launch downloads Python 3.12 and the libraries through [uv](https://docs.astral.sh/uv/)
(`brew install uv` if missing). The virtual environment lives in `~/.cache/echem-analyzer/venv`,
**outside Google Drive**, so it is not synced. You can also drag files, folders or a project onto the window.

## Tests

```sh
export UV_PROJECT_ENVIRONMENT="$HOME/.cache/echem-analyzer/venv"
uv run pytest                                       # I/O, maths, projects, presets, report / PDF / Excel, and the GUI driven offscreen
ECHEM_SHOT_DIR=/some/dir uv run pytest tests/test_gui_smoke.py tests/test_gui_project.py   # also saves screenshots
```

The suite also fails on any exception raised inside a Qt slot (PySide would otherwise print it and carry on), and the
plot / report / project / workbook code is tested to run without Qt.

Tests that need your real data use the sibling `Fabrication & Profilometry/2026-11-02 LPA GY CSF data` folder and are
skipped if it is missing. GUI tests work on temporary copies, with their settings and presets redirected to a temp
folder, so they never touch your data or your real configuration.

## Versioning and releases

Versions follow [Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`); every release is an annotated git tag `vX.Y.Z`, and
[CHANGELOG.md](../CHANGELOG.md) says what changed. The version lives in one place, `__version__` in `echem_analyzer/__init__.py`;
`python -m echem_analyzer --version` prints it and the Help ▸ About box, reports and projects record it.

To release:

1. Bump `__version__`; move the *Unreleased* notes in `CHANGELOG.md` under `## [X.Y.Z] - YYYY-MM-DD` (and add the link at the bottom).
2. Commit both: `git commit -am "chore(release): X.Y.Z"`.
3. `scripts/release.sh X.Y.Z --push`: pushing the tag also starts the installer build and the GitHub Release (see *Building the installers*). It checks you are on a clean `main`, that the version and the changelog agree and that the tag is new,
   runs the tests, creates the annotated tag with the changelog section as its message and pushes `main` and that tag.
   Without `--push` it only tags; the commit to publish is printed.

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/): `feat` (minor bump), `fix` (patch), `feat!` or a
`BREAKING CHANGE:` footer (major), and `docs`, `test`, `chore`, `build`, `refactor` for the rest.

## Building the installers

The installers are built with [PyInstaller](https://pyinstaller.org) by one script that is used both on your machine and in CI:

```sh
uv sync --group build
uv run python packaging/build.py                # freeze, smoke-test the frozen app, make this system's installer in dist/release/
uv run python packaging/build.py --no-installer # only freeze and smoke-test (about a minute)
```

PyInstaller cannot cross-compile, so each installer is built on its own system: that is what the GitHub Actions workflow does.
What the script produces: a `.dmg` on macOS (`hdiutil`), the Inno Setup installer and a portable zip on Windows (the installer needs
[Inno Setup](https://jrsoftware.org/isdl.php); without it only the zip is made), and a `.deb` on Linux (`dpkg-deb`).
The frozen app is smoke-tested before it is packaged: `--version`, then `--selftest`, which analyses a synthetic calibration, writes a PDF,
an Excel workbook and a project, reads the project back and draws every tab, all without a display
(`QT_QPA_PLATFORM=offscreen echem-analyzer --selftest`). A missing library or plugin therefore fails the build, not the user.

Files: `packaging/echem-analyzer.spec` (what goes into the bundle), `packaging/build.py` (the build), `packaging/windows/echem-analyzer.iss`
(the Windows installer), `assets/` (the icon; `uv run python packaging/make_icons.py` regenerates the PNG, ICO and ICNS from `icon.svg`).

### Releasing with GitHub Actions

`.github/workflows/ci.yml` runs the tests on every push and pull request. `.github/workflows/release.yml` builds and publishes:

1. Bump `__version__`, update `CHANGELOG.md`, commit (see *Versioning and releases*), then `scripts/release.sh X.Y.Z --push`.
2. The pushed tag `vX.Y.Z` starts the workflow. It checks that the tag equals the version and that the changelog has a section for it,
   runs the tests, then builds on four GitHub runners (macOS Apple silicon, macOS Intel, Windows, Ubuntu 22.04), smoke-tests every
   package (the `.deb` is installed with `apt`, the Windows installer is run silently, and the installed program's self-test must pass)
   and uploads the installers.
3. If all builds pass, the **release** job (the only one allowed to write to the repository) creates the GitHub Release with the changelog
   section as its notes, the installers and `SHA256SUMS.txt`.

To try the builds without releasing anything, open *Actions ▸ Release ▸ Run workflow*: the installers are kept as downloadable build
artifacts for 14 days and no release is made. Do this once before the first real tag.

Runner names change over time (`macos-15-intel`, `ubuntu-22.04`, …); if a job fails to start, check GitHub's list of hosted runners.
Dependabot (`.github/dependabot.yml`) proposes updates to the action versions.

**Signing.** macOS: enrol in the Apple Developer Program, create a *Developer ID Application* certificate, and add signing and
`notarytool` notarization steps to the macOS jobs (secrets: the certificate as base64 and its password, an app-specific password, the
team id). Windows: buy a code-signing certificate (or use Azure Trusted Signing) and sign the installer and `EchemAnalyzer.exe` with
`signtool` before packaging. Neither is set up here: the macOS app is only ad-hoc signed (the default of PyInstaller), the Windows
installer is unsigned.
