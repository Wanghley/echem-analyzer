"""The author information, the support widgets (the coffee button, the support card, the signature) and how they sit in the window.

None of these tests needs the real data folder, and none opens a browser: ``support.open_link`` is replaced by a recorder.
"""

import re

import pytest

import echem_analyzer
from echem_analyzer import about, theme


@pytest.fixture(autouse=True)
def _light(qapp):
    yield
    theme.apply(qapp, "light")


def test_author_and_links_are_the_ones_the_owner_gave():
    assert about.AUTHOR == "Wanghley Soares Martins"
    assert about.COFFEE.url == "https://buymeacoffee.com/wanghley"
    assert about.WEBSITE.url == "https://wanghley.com"
    assert about.GITHUB.url == "https://github.com/Wanghley"
    assert about.LINKEDIN.url == "https://www.linkedin.com/in/wanghley/"
    assert about.INSTAGRAM.url == "https://www.instagram.com/wanghley/"


def test_the_source_has_no_other_coffee_or_profile_url():
    """A link typed into some other module would drift from ``about``: every URL in the GUI package must come from it."""
    import pathlib
    gui = pathlib.Path(echem_analyzer.__file__).parent / "gui"
    known = {l.url for l in about.ALL_LINKS}
    for path in gui.glob("*.py"):
        for url in re.findall(r"https?://[^\s\"')]+", path.read_text(encoding="utf-8")):
            assert url in known, f"{path.name}: {url}"


def test_the_package_metadata_names_the_author():
    import pathlib
    root = pathlib.Path(echem_analyzer.__file__).parents[1]
    assert about.AUTHOR in (root / "pyproject.toml").read_text(encoding="utf-8")
    assert about.AUTHOR in (root / "packaging" / "build.py").read_text(encoding="utf-8")
