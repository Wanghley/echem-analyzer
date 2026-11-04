"""Colour palettes and matplotlib styling (no Qt imports, so plots and reports also work headless)."""
from __future__ import annotations

from dataclasses import dataclass, replace

import matplotlib
from cycler import cycler
from matplotlib import font_manager

@dataclass(frozen=True)
class Palette:
    name: str
    # surfaces and lines
    bg: str
    surface: str
    surface2: str
    border: str
    border_strong: str
    # text
    text: str
    muted: str
    faint: str
    # semantic colours
    accent: str
    accent_soft: str
    accent_text: str
    success: str
    success_soft: str
    warning: str
    warning_soft: str
    danger: str
    danger_soft: str
    # plots
    plot_bg: str
    grid: str
    series: tuple[str, ...]
    trace: str
    smooth: str
    step: str
    window: str
    plateau: str
    fit: str
    extrap: str
    cx: str
    dropped: str
    excluded: str


LIGHT = Palette(
    name="light", bg="#F4F6F9", surface="#FFFFFF", surface2="#F1F4F8", border="#E1E6ED", border_strong="#C9D1DC",
    text="#18212F", muted="#5B6678", faint="#98A2B3",
    accent="#2563EB", accent_soft="#E6EEFD", accent_text="#FFFFFF",
    success="#15803D", success_soft="#E4F5EA", warning="#B45309", warning_soft="#FDF1DC",
    danger="#C62828", danger_soft="#FCE8E8",
    plot_bg="#FFFFFF", grid="#EBEEF3",
    series=("#2563EB", "#D9480F", "#0F9D58", "#7C3AED", "#0E7490", "#C2255C", "#4B5563", "#B08900"),
    trace="#2563EB", smooth="#1E3A8A", step="#D97706", window="#16A34A", plateau="#15803D", fit="#111827",
    extrap="#6B7280", cx="#C62828", dropped="#9CA3AF", excluded="#CBD5E1",
)

DARK = Palette(
    name="dark", bg="#0F141B", surface="#171D26", surface2="#1F2733", border="#2A3342", border_strong="#3A4558",
    text="#E7EBF1", muted="#9AA5B6", faint="#657185",
    accent="#5B9DFF", accent_soft="#1B2E4D", accent_text="#0B1220",
    success="#4ADE80", success_soft="#12301F", warning="#FBBF24", warning_soft="#33280E",
    danger="#F87171", danger_soft="#3A1A1A",
    plot_bg="#171D26", grid="#262F3D",
    series=("#5B9DFF", "#FB923C", "#4ADE80", "#A78BFA", "#22D3EE", "#F472B6", "#9CA3AF", "#FACC15"),
    trace="#60A5FA", smooth="#BFDBFE", step="#FBBF24", window="#4ADE80", plateau="#86EFAC", fit="#E5E7EB",
    extrap="#9CA3AF", cx="#F87171", dropped="#6B7280", excluded="#3B4656",
)

PALETTES = {"light": LIGHT, "dark": DARK}
MODES = ("system", "light", "dark")

# Plot colour variants: only the data colours change, never the window chrome.
# "colorblind" is the Okabe-Ito set (distinguishable with the common colour-vision deficiencies);
# "greyscale" survives black-and-white printing.
_VARIANTS = {
    ("colorblind", "light"): dict(
        series=("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#56B4E9", "#E69F00", "#444444", "#999999"),
        trace="#0072B2", smooth="#003B5C", step="#E69F00", window="#009E73", plateau="#009E73", fit="#111111", cx="#D55E00"),
    ("colorblind", "dark"): dict(
        series=("#56B4E9", "#E69F00", "#34D399", "#E8A0C9", "#F0E442", "#FF8A5B", "#D1D5DB", "#9CA3AF"),
        trace="#56B4E9", smooth="#BFE3F7", step="#E69F00", window="#34D399", plateau="#6EE7B7", fit="#E5E7EB", cx="#FF8A5B"),
    ("greyscale", "light"): dict(
        series=("#111111", "#666666", "#333333", "#999999", "#4D4D4D", "#BFBFBF", "#1F1F1F", "#808080"),
        trace="#111111", smooth="#000000", step="#666666", window="#999999", plateau="#4D4D4D", fit="#000000", cx="#000000"),
    ("greyscale", "dark"): dict(
        series=("#F2F2F2", "#999999", "#D0D0D0", "#6B6B6B", "#E5E5E5", "#4D4D4D", "#FAFAFA", "#8A8A8A"),
        trace="#F2F2F2", smooth="#FFFFFF", step="#999999", window="#6B6B6B", plateau="#D0D0D0", fit="#FFFFFF", cx="#FFFFFF"),
}


def styled(p: Palette, variant: str = "default") -> Palette:
    """``p`` with the data colours of a plot palette variant ("default", "colorblind" or "greyscale")."""
    over = _VARIANTS.get((variant, p.name))
    return replace(p, **over) if over else p


def report_palette() -> Palette:
    """The palette exports are drawn with: always light (print), with the user's plot palette."""
    from .prefs import prefs
    return styled(LIGHT, prefs.get("plot_palette"))


# --------------------------------------------------------------------------- matplotlib

def _font_family() -> list[str]:
    """The first installed preferred font, then DejaVu Sans (bundled with matplotlib) so glyphs the first
    font lacks (arrows, subscripts, Greek) fall back instead of rendering as empty boxes."""
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Helvetica Neue", "Helvetica", "Arial", "Segoe UI", "Inter"):
        if name in installed:
            return [name, "DejaVu Sans"]
    return ["DejaVu Sans"]


_FONT_FAMILY = _font_family()


_PUBLICATION_FONT = ["STIXGeneral", "DejaVu Serif", "DejaVu Sans"]       # STIX ships with matplotlib: same look everywhere


def mpl_rc(p: Palette) -> dict:
    """matplotlib rcParams for a palette and the user's plot style (use with ``matplotlib.rc_context`` off-screen)."""
    from .prefs import prefs
    rc = _base_rc(p)
    if prefs.get("plot_style") == "publication":
        # boxed axes, inward ticks on all sides with minor ticks, serif text, no grid: the look of a journal figure
        rc.update({
            "font.family": _PUBLICATION_FONT, "mathtext.fontset": "stix",
            "axes.spines.top": True, "axes.spines.right": True, "axes.grid": False,
            "axes.edgecolor": p.text, "axes.labelcolor": p.text, "axes.linewidth": 1.0, "axes.titleweight": "normal",
            "xtick.color": p.text, "ytick.color": p.text, "xtick.direction": "in", "ytick.direction": "in",
            "xtick.top": True, "ytick.right": True, "xtick.minor.visible": True, "ytick.minor.visible": True,
            "xtick.major.width": 1.0, "ytick.major.width": 1.0, "xtick.minor.width": 0.7, "ytick.minor.width": 0.7,
            "xtick.major.size": 4, "ytick.major.size": 4, "xtick.minor.size": 2, "ytick.minor.size": 2,
        })
    scale = float(prefs.get("plot_font_scale"))
    if scale != 1.0:
        for k in ("axes.titlesize", "axes.labelsize", "xtick.labelsize", "ytick.labelsize", "legend.fontsize"):
            rc[k] = rc[k] * scale
    return rc


def _base_rc(p: Palette) -> dict:
    return {
        "figure.facecolor": p.plot_bg, "savefig.facecolor": p.plot_bg, "axes.facecolor": p.plot_bg,
        "axes.edgecolor": p.border_strong, "axes.linewidth": 0.8, "axes.labelcolor": p.muted,
        "axes.titlecolor": p.text, "axes.titlesize": 10.5, "axes.titleweight": "bold",
        "axes.labelsize": 9, "axes.titlepad": 8, "axes.labelpad": 5,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.axisbelow": True, "axes.grid": True, "grid.color": p.grid, "grid.linewidth": 0.8, "grid.alpha": 1.0,
        "axes.prop_cycle": cycler(color=list(p.series)),
        "text.color": p.text, "xtick.color": p.muted, "ytick.color": p.muted,
        "xtick.labelsize": 8.5, "ytick.labelsize": 8.5, "xtick.major.size": 3, "ytick.major.size": 3,
        "xtick.major.width": 0.8, "ytick.major.width": 0.8,
        # keys the publication style changes, stated here too: rcParams.update() never removes a key, so without them
        # switching back to the modern style would leave inward / minor / top ticks behind
        "xtick.direction": "out", "ytick.direction": "out", "xtick.top": False, "ytick.right": False,
        "xtick.minor.visible": False, "ytick.minor.visible": False, "xtick.minor.width": 0.6, "ytick.minor.width": 0.6,
        "xtick.minor.size": 2, "ytick.minor.size": 2, "mathtext.fontset": "dejavusans",
        "font.family": _FONT_FAMILY,
        "legend.frameon": False, "legend.fontsize": 8.5, "legend.labelcolor": p.text,
        "lines.linewidth": 1.5, "lines.solid_capstyle": "round",
        "savefig.dpi": 200, "figure.dpi": 100,
    }


def apply_mpl(p: Palette) -> None:
    matplotlib.rcParams.update(mpl_rc(p))
