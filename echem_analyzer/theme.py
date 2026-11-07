"""Design system: colour palettes (light / dark), Qt style sheet, matplotlib style and icons.

Everything visual comes from the tokens in :class:`Palette`, so the window, the plots on screen and the
plots written into reports share one visual language, and switching light / dark changes all of them.
Reports and project snapshots are always drawn with the LIGHT palette (print-friendly).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QByteArray, QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QStyleFactory

from .about import COFFEE_BG, COFFEE_BG_HOVER, COFFEE_FG
from .palette import DARK, LIGHT, MODES, PALETTES, Palette, apply_mpl, mpl_rc, report_palette, styled  # noqa: F401 (re-exported)
from .prefs import prefs

_current: Palette = LIGHT
_pref: str = "system"


class _Bus(QObject):
    changed = Signal()                      # emitted after the theme was switched


bus = _Bus()


def current() -> Palette:
    return _current


def preference() -> str:
    return _pref


def system_is_dark(app: Optional[QApplication] = None) -> bool:
    app = app or QApplication.instance()
    try:
        return app is not None and app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:                  # very old Qt
        return False


def resolve(pref: str) -> Palette:
    if pref == "system":
        return DARK if system_is_dark() else LIGHT
    return PALETTES.get(pref, LIGHT)


# --------------------------------------------------------------------------- Qt

def qpalette(p: Palette) -> QPalette:
    pal = QPalette()
    c = QColor
    roles = {
        QPalette.Window: p.bg, QPalette.WindowText: p.text, QPalette.Base: p.surface,
        QPalette.AlternateBase: p.surface2, QPalette.Text: p.text, QPalette.Button: p.surface,
        QPalette.ButtonText: p.text, QPalette.Highlight: p.accent, QPalette.HighlightedText: p.accent_text,
        QPalette.ToolTipBase: p.surface2, QPalette.ToolTipText: p.text, QPalette.PlaceholderText: p.faint,
        QPalette.Link: p.accent, QPalette.BrightText: p.danger, QPalette.Mid: p.border,
        QPalette.Midlight: p.border, QPalette.Dark: p.border_strong, QPalette.Light: p.surface,
    }
    for role, col in roles.items():
        pal.setColor(role, c(col))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, c(p.faint))
    return pal


def _arrow_files(color: str) -> dict[str, str]:
    """PNG chevrons (``down``, ``up``) in ``color`` for the combo and spin boxes, as style-sheet-safe paths.

    Written once per colour into the temp folder (a 1x file and an ``@2x`` one that Qt picks on high-DPI screens).  An empty
    dict when that folder is not writable: the style sheet then leaves the arrows to the platform."""
    out: dict[str, str] = {}
    try:
        folder = Path(tempfile.gettempdir()) / "echem-analyzer-ui"
        folder.mkdir(parents=True, exist_ok=True)
        tag = color.lstrip("#").lower()
        for key, icon_name in (("down", "chevron-down"), ("up", "chevron-up")):
            one, two = folder / f"{icon_name}-{tag}.png", folder / f"{icon_name}-{tag}@2x.png"
            if not (one.exists() and two.exists()):
                for path, px in ((one, 12), (two, 24)):
                    r = QSvgRenderer(QByteArray(_svg(icon_name, color, px)))
                    pm = QPixmap(px, px)
                    pm.fill(Qt.transparent)
                    pt = QPainter(pm)
                    pt.setRenderHint(QPainter.Antialiasing)
                    r.render(pt, QRectF(0, 0, px, px))
                    pt.end()
                    if not pm.save(str(path), "PNG"):
                        return {}
            out[key] = one.as_posix()
    except OSError:
        return {}
    return out


def shade(color: str, factor: int) -> str:
    """``color`` darkened by Qt's rule: ``shade(c, 115)`` is ``QColor(c).darker(115)`` (100 = unchanged)."""
    return QColor(color).darker(factor).name()


def lighten(color: str, factor: int) -> str:
    """``color`` lightened by Qt's rule: ``lighten(c, 110)`` is ``QColor(c).lighter(110)`` (100 = unchanged)."""
    return QColor(color).lighter(factor).name()


def stylesheet(p: Palette) -> str:
    dark = p.name == "dark"
    primary_top = lighten(p.accent, 108) if not dark else lighten(p.accent, 112)
    primary_bottom = shade(p.accent, 108) if not dark else shade(p.accent, 104)
    primary_hover_top, primary_hover_bottom = lighten(primary_top, 110), lighten(primary_bottom, 108)
    arrows = _arrow_files(p.muted)
    arrow_rules = ""
    if arrows:
        arrow_rules = f"""
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 26px; border: none; }}
QComboBox::down-arrow {{ image: url("{arrows['down']}"); width: 12px; height: 12px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 22px; border: none;
                                                  background: transparent; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 22px;
                                                      border: none; background: transparent; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{arrows['up']}"); width: 9px; height: 9px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{arrows['down']}"); width: 9px; height: 9px; }}
"""
    return f"""
* {{ outline: 0; }}
QMainWindow, QDialog {{ background: {p.bg}; }}
QToolTip {{ background: {p.surface2}; color: {p.text}; border: 1px solid {p.border_strong};
           padding: 5px 8px; border-radius: 6px; }}

/* ---- toolbar ---- */
QToolBar#MainToolbar {{ background: {p.surface}; border: none; border-bottom: 1px solid {p.border};
                        spacing: 2px; padding: 6px 12px; }}
QToolBar#MainToolbar QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 7px;
                                    padding: 6px 10px; color: {p.text}; }}
QToolBar#MainToolbar QToolButton:hover {{ background: {p.surface2}; border-color: {p.border}; }}
QToolBar#MainToolbar QToolButton:pressed {{ background: {p.accent_soft}; }}
QToolBar#MainToolbar QToolButton::menu-indicator {{ image: none; width: 0; }}
QToolBar#MainToolbar QFrame#ToolSep {{ background: {p.border}; border: none; max-width: 1px; margin: 7px 8px; }}
QFrame#ProjectHeader {{ background: transparent; border: none; border-bottom: 1px solid {p.border}; }}
QFrame#ProjectHeader QLabel {{ background: transparent; }}
QLabel#ProjectName {{ font-size: 13.5px; font-weight: 650; color: {p.text}; }}
QLabel#ProjectSub {{ color: {p.muted}; font-size: 11px; }}
QFrame#ProjectHeader QLabel#ProjectChip {{ background: {p.accent_soft}; border-radius: 9px; }}

/* ---- tabs ---- */
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {p.muted}; padding: 9px 16px 8px 16px; margin-right: 2px;
               border: none; border-bottom: 2px solid transparent; font-weight: 500; }}
QTabBar::tab:hover:!selected {{ color: {p.text}; }}
QTabBar::tab:selected {{ color: {p.accent}; border-bottom: 2px solid {p.accent}; font-weight: 600; }}
QTabWidget#SubTabs QTabBar::tab {{ padding: 6px 12px 5px 12px; font-size: 12px; }}

/* ---- cards ---- */
QFrame#Card {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px; }}
QFrame#Card QLabel {{ background: transparent; }}
QLabel#CardTitle {{ color: {p.muted}; font-size: 10.5px; font-weight: 700; }}
QPushButton#CardToggle {{ background: transparent; border: none; color: {p.muted}; font-size: 10.5px;
                          font-weight: 700; text-align: left; padding: 2px 0; }}
QPushButton#CardToggle:hover {{ color: {p.text}; background: transparent; }}
QLabel#KpiLabel {{ color: {p.muted}; font-size: 10.5px; font-weight: 600; }}
QLabel#KpiValue {{ font-size: 21px; font-weight: 650; color: {p.text}; }}
QLabel#KpiSub {{ color: {p.muted}; font-size: 10.5px; }}
QLabel#Hint {{ color: {p.muted}; font-size: 11px; }}
QLabel#Badge {{ border-radius: 9px; padding: 2px 10px; font-size: 11px; font-weight: 600; }}
QLabel#Badge[tone="success"] {{ background: {p.success_soft}; color: {p.success}; }}
QLabel#Badge[tone="warning"] {{ background: {p.warning_soft}; color: {p.warning}; }}
QLabel#Badge[tone="danger"] {{ background: {p.danger_soft}; color: {p.danger}; }}
QLabel#Badge[tone="neutral"] {{ background: {p.surface2}; color: {p.muted}; }}
QLabel#Notice {{ border-radius: 8px; padding: 8px 10px; font-size: 11.5px; }}
QLabel#Notice[tone="warning"] {{ background: {p.warning_soft}; color: {p.warning}; }}
QLabel#Notice[tone="success"] {{ background: {p.success_soft}; color: {p.success}; }}
QLabel#Notice[tone="neutral"] {{ background: {p.surface2}; color: {p.muted}; }}

/* ---- welcome page ---- */
QLabel#WelcomeTitle {{ font-size: 28px; font-weight: 700; color: {p.text}; }}
QLabel#WelcomeSub {{ font-size: 13px; color: {p.muted}; }}
QPushButton#Tile {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px; text-align: left; padding: 0; }}
QPushButton#Tile:hover {{ background: {p.accent_soft}; border-color: {p.accent}; }}
QPushButton#LinkButton {{ background: transparent; border: none; color: {p.accent}; padding: 4px 8px; }}
QPushButton#LinkButton:hover {{ text-decoration: underline; background: transparent; }}

/* ---- settings and help windows ---- */
QListWidget#NavList, QTreeWidget#NavList {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px; padding: 6px;
                                            outline: none; }}
QListWidget#NavList::item {{ padding: 9px 12px; border-radius: 7px; color: {p.text}; }}
QListWidget#NavList::item:selected, QTreeWidget#NavList::item:selected {{ background: {p.accent_soft}; color: {p.accent}; font-weight: 600; }}
QTextBrowser#GuideView {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px; padding: 14px; }}
QLabel#SettingsTitle {{ font-size: 18px; font-weight: 700; color: {p.text}; }}
QLabel#SettingsHint {{ color: {p.muted}; font-size: 11.5px; }}

/* ---- buttons ---- */
QPushButton, QToolButton#Soft {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border_strong};
                                 border-radius: 7px; padding: 5px 12px; }}
QPushButton:hover, QToolButton#Soft:hover {{ background: {p.surface2}; }}
QPushButton:pressed {{ background: {p.accent_soft}; }}
QPushButton:disabled {{ color: {p.faint}; border-color: {p.border}; background: {p.surface}; }}
QPushButton[primary="true"] {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {primary_top}, stop:1 {primary_bottom});
                               color: {p.accent_text}; border: 1px solid {primary_bottom}; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {primary_hover_top}, stop:1 {primary_hover_bottom});
                                     border-color: {primary_bottom}; }}
QPushButton[primary="true"]:pressed {{ background: {primary_bottom}; }}
QPushButton[primary="true"]:disabled {{ background: {p.surface2}; color: {p.faint}; border-color: {p.border}; }}
QPushButton[flat="true"] {{ background: transparent; border: 1px solid transparent; color: {p.accent}; }}
QPushButton[flat="true"]:hover {{ background: {p.accent_soft}; }}

/* ---- support: the coffee button, link chips, the support card and the About window ---- */
QPushButton#Coffee {{ background: {COFFEE_BG}; color: {COFFEE_FG}; border: 1px solid #E3C400; border-radius: 17px;
                      padding: 7px 18px 7px 14px; font-weight: 650; }}
QPushButton#Coffee:hover {{ background: {COFFEE_BG_HOVER}; border-color: #E3C400; }}
QPushButton#Coffee:pressed {{ background: #F2CF00; }}
QPushButton#Coffee[compact="true"] {{ padding: 4px 13px 4px 10px; border-radius: 15px; font-size: 12px; }}
QPushButton#Chip {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border_strong}; border-radius: 14px;
                    padding: 6px 14px 6px 10px; font-weight: 500; }}
QPushButton#Chip:hover {{ background: {p.accent_soft}; border-color: {p.accent}; color: {p.accent}; }}
QPushButton#IconLink {{ background: transparent; border: 1px solid transparent; border-radius: 14px; padding: 5px; }}
QPushButton#IconLink:hover {{ background: {p.accent_soft}; border-color: {p.border}; }}
QPushButton#CloseX {{ background: transparent; border: none; border-radius: 11px; padding: 3px; }}
QPushButton#CloseX:hover {{ background: {p.surface2}; }}
QFrame#SupportCard {{ background: {p.surface}; border: 1px solid {p.border_strong}; border-radius: 16px; }}
QFrame#SupportCard QLabel {{ background: transparent; }}
QLabel#SupportTitle {{ font-size: 14px; font-weight: 700; color: {p.text}; }}
QLabel#SupportText {{ font-size: 12px; color: {p.muted}; }}
QFrame#SupportCard QLabel#CoffeeChip {{ background: {COFFEE_BG}; border-radius: 14px; }}
QPushButton#QuietLink {{ background: transparent; border: none; color: {p.muted}; font-size: 11px; padding: 2px 4px; }}
QPushButton#QuietLink:hover {{ color: {p.text}; text-decoration: underline; background: transparent; }}
QLabel#HeroLogo {{ background: rgba(255, 255, 255, 34); border: 1px solid rgba(255, 255, 255, 64); border-radius: 24px; }}
QLabel#HeroTitle {{ color: #FFFFFF; font-size: 27px; font-weight: 750; background: transparent; }}
QLabel#HeroSub {{ color: rgba(255, 255, 255, 215); font-size: 12.5px; background: transparent; }}
QLabel#HeroBadge {{ color: #FFFFFF; background: rgba(255, 255, 255, 38); border: 1px solid rgba(255, 255, 255, 70);
                    border-radius: 10px; padding: 2px 10px; font-size: 11px; font-weight: 650; }}
QLabel#AuthorName {{ font-size: 17px; font-weight: 700; color: {p.text}; background: transparent; }}
QLabel#AuthorRole {{ font-size: 12px; color: {p.muted}; background: transparent; }}

/* ---- start page ---- */
QPushButton#ActionRow {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px; text-align: left; padding: 0; }}
QPushButton#ActionRow:hover {{ background: {p.accent_soft}; border-color: {p.accent}; }}
QPushButton#ActionRow[primary="true"] {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {primary_top}, stop:1 {primary_bottom});
                                         border-color: {primary_bottom}; }}
QPushButton#ActionRow[primary="true"]:hover {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {primary_hover_top}, stop:1 {primary_hover_bottom});
                                               border-color: {primary_bottom}; }}
QLabel#ActionIcon {{ background: {p.accent_soft}; border-radius: 10px; }}
QPushButton#ActionRow[primary="true"] QLabel#ActionIcon {{ background: rgba(255, 255, 255, 46); }}
QLabel#ActionTitle {{ font-size: 13.5px; font-weight: 650; color: {p.text}; background: transparent; }}
QLabel#ActionSub {{ font-size: 11.5px; color: {p.muted}; background: transparent; }}
QPushButton#ActionRow[primary="true"] QLabel#ActionTitle {{ color: {p.accent_text}; }}
QPushButton#ActionRow[primary="true"] QLabel#ActionSub {{ color: {p.accent_text}; }}
QLabel#KeyCap {{ background: {p.surface2}; border: 1px solid {p.border}; border-radius: 6px; padding: 1px 7px; color: {p.muted};
                 font-size: 11px; font-weight: 600; }}
QPushButton#ActionRow[primary="true"] QLabel#KeyCap {{ background: rgba(255, 255, 255, 40); border-color: rgba(255, 255, 255, 70);
                                                       color: {p.accent_text}; }}
QLabel#SectionLabel {{ color: {p.faint}; font-size: 10.5px; font-weight: 700; }}
QLabel#VersionTag {{ color: {p.faint}; font-size: 11px; font-weight: 600; }}
QLabel#DropHint {{ color: {p.faint}; font-size: 12px; }}
QLabel#TileGlyph {{ background: transparent; }}
QLabel#TileName {{ font-size: 12.5px; font-weight: 600; color: {p.text}; background: transparent; }}
QFrame#PageFooter {{ background: {p.surface}; border: none; border-top: 1px solid {p.border}; }}
QFrame#PageFooter QLabel {{ background: transparent; }}
QLabel#Signature {{ color: {p.muted}; font-size: 12px; background: transparent; }}
/* ---- updates and version information (About) ---- */
QFrame#Card QLabel#UpdateChip[tone="neutral"] {{ background: {p.surface2}; border-radius: 18px; }}
QFrame#Card QLabel#UpdateChip[tone="accent"] {{ background: {p.accent_soft}; border-radius: 18px; }}
QFrame#Card QLabel#UpdateChip[tone="success"] {{ background: {p.success_soft}; border-radius: 18px; }}
QFrame#Card QLabel#UpdateChip[tone="danger"] {{ background: {p.danger_soft}; border-radius: 18px; }}
QLabel#UpdateTitle {{ font-size: 14px; font-weight: 700; color: {p.text}; background: transparent; }}
QLabel#UpdateDetail {{ font-size: 12px; color: {p.muted}; background: transparent; }}
QLabel#InfoKey {{ color: {p.muted}; font-size: 12px; background: transparent; }}
QLabel#InfoValue {{ color: {p.text}; font-size: 12px; background: transparent; }}
QTextBrowser#Notes {{ background: {p.surface2}; border: 1px solid {p.border}; border-radius: 8px; padding: 6px; font-size: 12px; }}

/* ---- form controls ---- */
QComboBox, QSpinBox, QDoubleSpinBox {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border_strong};
                                       border-radius: 7px; padding: 3px 9px; min-height: 20px;
                                       selection-background-color: {p.accent}; selection-color: {p.accent_text}; }}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {p.faint}; }}
QComboBox:focus, QComboBox:on, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {p.accent}; }}
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {p.faint}; background: {p.surface2}; border-color: {p.border}; }}
QComboBox QAbstractItemView {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border_strong}; border-radius: 8px;
                               padding: 4px; outline: 0; selection-background-color: {p.accent_soft}; selection-color: {p.text}; }}
QTableWidget QComboBox {{ border-radius: 5px; padding: 1px 8px; min-height: 18px; }}
QProgressBar {{ background: {p.surface2}; border: none; border-radius: 5px; color: {p.muted}; text-align: center;
                min-height: 10px; max-height: 10px; font-size: 10px; }}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 5px; }}
QSlider::groove:horizontal {{ background: {p.border_strong}; height: 4px; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {p.surface}; border: 2px solid {p.accent}; width: 12px; height: 12px;
                              margin: -7px 0; border-radius: 8px; }}
QSlider::handle:horizontal:hover {{ background: {p.accent_soft}; }}

/* ---- inputs ---- */
QLineEdit, QPlainTextEdit, QTextEdit {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border_strong};
                                        border-radius: 7px; padding: 4px 8px; selection-background-color: {p.accent};
                                        selection-color: {p.accent_text}; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {p.accent}; }}

/* ---- item views ---- */
QTableWidget, QTreeWidget, QListWidget {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border};
                                          border-radius: 10px; gridline-color: transparent;
                                          alternate-background-color: {p.surface2};
                                          selection-background-color: {p.accent_soft}; selection-color: {p.text}; }}
QTableWidget::item {{ padding: 2px 4px; }}
QTreeWidget::item, QListWidget::item {{ padding: 3px 4px; }}
QTreeWidget::item:selected, QListWidget::item:selected, QTableWidget::item:selected {{
    background: {p.accent_soft}; color: {p.text}; }}
QHeaderView::section {{ background: {p.surface2}; color: {p.muted}; border: none; border-bottom: 1px solid {p.border};
                        padding: 6px 5px; font-size: 10.5px; font-weight: 700; }}
QTableCornerButton::section {{ background: {p.surface2}; border: none; }}

/* ---- scrolling ---- */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px 1px; }}
QScrollBar::handle:vertical {{ background: {p.border_strong}; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {p.faint}; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 1px 2px; }}
QScrollBar::handle:horizontal {{ background: {p.border_strong}; border-radius: 4px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {p.faint}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- chrome ---- */
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 8px; }}
QSplitter::handle:vertical {{ height: 8px; }}
QMainWindow::separator {{ background: transparent; width: 8px; height: 8px; }}
QMainWindow::separator:hover {{ background: {p.accent_soft}; }}
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {p.bg}; padding: 10px 12px 6px 12px; color: {p.muted}; font-weight: 700;
                      font-size: 10.5px; text-align: left; }}
QStatusBar {{ background: {p.surface}; color: {p.muted}; border-top: 1px solid {p.border}; }}
QStatusBar::item {{ border: none; }}
QMenuBar {{ background: {p.surface}; color: {p.text}; }}
QMenuBar::item:selected {{ background: {p.accent_soft}; border-radius: 4px; }}
QMenu {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border_strong}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 5px; }}
QMenu::item:selected {{ background: {p.accent_soft}; color: {p.text}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 4px 8px; }}
QGroupBox {{ border: 1px solid {p.border}; border-radius: 10px; margin-top: 10px; padding-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {p.muted}; font-weight: 600; }}
QProgressDialog {{ background: {p.bg}; }}
{arrow_rules}"""


def refresh_plots() -> None:
    """Re-read the plot preferences (style, palette, text size) and redraw every plot; the window chrome is untouched."""
    global _current
    _current = styled(resolve(_pref), prefs.get("plot_palette"))
    apply_mpl(_current)
    bus.changed.emit()


def apply(app: QApplication, pref: str = "system") -> Palette:
    """Apply the Fusion style, palette, style sheet and matplotlib style for ``pref``."""
    global _current, _pref
    _pref = pref if pref in MODES else "system"
    new = styled(resolve(_pref), prefs.get("plot_palette"))
    if new == _current and app.styleSheet():
        return _current                       # already applied: don't re-polish every widget or redraw every plot
    _current = new
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setPalette(qpalette(_current))
    app.setStyleSheet(stylesheet(_current))
    apply_mpl(_current)
    bus.changed.emit()
    return _current


# --------------------------------------------------------------------------- icons

_SVG = {
    "folder-open": '<path d="M6 14l1.5-2.9A2 2 0 0 1 9.3 10H20a2 2 0 0 1 1.9 2.6l-1.6 5.2a2 2 0 0 1-1.9 1.4H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.7.9l.8 1.2a2 2 0 0 0 1.7.9H18a2 2 0 0 1 2 2v2"/>',
    "save": '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "file-text": '<path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><line x1="10" y1="9" x2="8" y2="9"/>',
    "table": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    "activity": '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
    "info": '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
    "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9l-.8-1.2A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2z"/>',
    "help-circle": '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    "layers": '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
    "repeat": '<polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>',
    "sliders": '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
    "zap": '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    "bar-chart": '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
    "columns": '<path d="M12 3h7a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-7m0-18H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h7m0-18v18"/>',
    "search": '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "home": '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
    "x-circle": '<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>',
    "globe": '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    "github": '<path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/>',
    "linkedin": '<path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-2-2 2 2 0 0 0-2 2v7h-4v-7a6 6 0 0 1 6-6z"/><rect x="2" y="9" width="4" height="12"/><circle cx="4" cy="4" r="2"/>',
    "instagram": '<rect x="2" y="2" width="20" height="20" rx="5" ry="5"/><path d="M16 11.37A4 4 0 1 1 12.63 8 4 4 0 0 1 16 11.37z"/><line x1="17.5" y1="6.5" x2="17.51" y2="6.5"/>',
    "coffee": '<path d="M18 8h1a4 4 0 0 1 0 8h-1"/><path d="M2 8h16v9a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4V8z"/><line x1="6" y1="1" x2="6" y2="4"/><line x1="10" y1="1" x2="10" y2="4"/><line x1="14" y1="1" x2="14" y2="4"/>',
    "external-link": '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>',
    "x": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    "chevron-down": '<polyline points="6 9 12 15 18 9"/>',
    "chevron-up": '<polyline points="6 15 12 9 18 15"/>',
    "heart-fill": '<path fill="@C@" d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>',
    "arrow-up-circle": '<circle cx="12" cy="12" r="10"/><polyline points="16 12 12 8 8 12"/><line x1="12" y1="16" x2="12" y2="8"/>',
    "refresh-cw": '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
    "check-circle": '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',
    "alert-triangle": '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "copy": '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
}


def _svg(name: str, color: str, size: int) -> bytes:
    body = _SVG[name].replace("@C@", color)           # icons that are filled shapes name their colour with @C@
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode()


def pixmap(name: str, color: Optional[str] = None, size: int = 18, dpr: float = 2.0) -> QPixmap:
    color = color or _current.text
    r = QSvgRenderer(QByteArray(_svg(name, color, size)))
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, size, size))
    p.end()
    return pm


def icon(name: str, color: Optional[str] = None, size: int = 18) -> QIcon:
    return QIcon(pixmap(name, color, size))


def app_logo(size: int = 64) -> QPixmap:
    """Rounded accent tile carrying the pulse glyph: the app icon and the welcome-page mark."""
    p = _current
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 64 64">'
           f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3B82F6"/>'
           f'<stop offset="1" stop-color="#1D4ED8"/></linearGradient></defs>'
           f'<rect width="64" height="64" rx="15" fill="url(#g)"/>'
           f'<g transform="translate(14 14) scale(1.5)" fill="none" stroke="#FFFFFF" stroke-width="2" '
           f'stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></g>'
           f'</svg>').encode()
    r = QSvgRenderer(QByteArray(svg))
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    pt = QPainter(pm)
    pt.setRenderHint(QPainter.Antialiasing)
    r.render(pt, QRectF(0, 0, size, size))
    pt.end()
    return pm


def is_mac() -> bool:
    return sys.platform == "darwin"
