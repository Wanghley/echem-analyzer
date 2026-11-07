"""Who made Echem Analyzer, and where to find them (no Qt: the one place these facts live).

The About window, the start page, the toolbar and the support card all read from here, so a link or a name is
changed once.  Every URL is a constant: the app never builds a link from user data.
"""
from __future__ import annotations

from dataclasses import dataclass

AUTHOR = "Wanghley Soares Martins"
AUTHOR_INITIALS = "WM"
AUTHOR_ROLE = "Creator and maintainer"
AUTHOR_EMAIL = "me@wanghley.com"


@dataclass(frozen=True)
class Link:
    key: str
    label: str
    url: str
    icon: str                     # a name in ``theme._SVG``


WEBSITE = Link("website", "Website", "https://wanghley.com", "globe")
GITHUB = Link("github", "GitHub", "https://github.com/Wanghley", "github")
LINKEDIN = Link("linkedin", "LinkedIn", "https://www.linkedin.com/in/wanghley/", "linkedin")
INSTAGRAM = Link("instagram", "Instagram", "https://www.instagram.com/wanghley/", "instagram")
COFFEE = Link("coffee", "Buy me a coffee", "https://buymeacoffee.com/wanghley", "coffee")
PROJECT = Link("project", "Source and releases", "https://github.com/Wanghley/echem-analyzer", "github")

SOCIALS: tuple[Link, ...] = (WEBSITE, GITHUB, LINKEDIN, INSTAGRAM)
ALL_LINKS: tuple[Link, ...] = SOCIALS + (COFFEE, PROJECT)

# Buy Me a Coffee's own brand colours: the button keeps them in light and dark so it is recognisable at a glance.
COFFEE_BG = "#FFDD00"
COFFEE_BG_HOVER = "#FFE84D"
COFFEE_FG = "#0D0C22"

PITCH = ("Echem Analyzer is free and is built and maintained in spare time. "
         "If it saved you an afternoon at the bench, a coffee helps keep it going.")
