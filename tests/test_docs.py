"""The README and the docs: every relative link, image and anchor must point at something that exists."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, emoji and punctuation dropped, spaces to hyphens."""
    text = re.sub(r"[`*_]", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"#{1,6}\s+(.*)", line)
        if m:
            out.add(slug(m.group(1)))
    return out


def references(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    refs = re.findall(r"\]\(([^)\s]+)\)", text) + re.findall(r'(?:src|href)="([^"]+)"', text)
    return [r for r in refs if not re.match(r"[a-z][a-z0-9+.-]*:", r) and not r.startswith("mailto:")]


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_relative_link_image_and_anchor_exists(doc):
    problems = []
    for ref in references(doc):
        target, _, fragment = ref.partition("#")
        file = doc if not target else (doc.parent / target).resolve()
        if not file.exists():
            problems.append(f"{ref}: no such file")
        elif fragment and file.suffix == ".md" and fragment not in anchors(file):
            problems.append(f"{ref}: no heading #{fragment} in {file.name}")
    assert not problems, "\n".join(problems)


def test_the_readme_is_the_front_door_and_points_to_the_deep_guides():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for guide in ("docs/user-guide.md", "docs/analysis-methods.md", "docs/development.md", "CHANGELOG.md"):
        assert guide in text and (ROOT / guide).exists()
    assert len(text.splitlines()) < 200                                         # the long method and developer text lives in docs/
    assert "Wanghley Soares Martins" in text and "buymeacoffee.com/wanghley" in text


def test_screenshots_are_real_files_and_light_enough_to_keep_in_the_repository():
    images = sorted((ROOT / "docs" / "images").glob("*.png"))
    assert {p.name for p in images} >= {"analysis.png", "study.png", "about.png", "home-light.png", "home-dark.png"}
    for p in images:
        assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and p.stat().st_size < 400_000, p.name


def test_nothing_in_the_docs_names_a_personal_folder():
    for doc in DOCS:
        assert "/Users/wanghley" not in doc.read_text(encoding="utf-8"), doc.name
