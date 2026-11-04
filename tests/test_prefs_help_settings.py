"""Preferences, plot styles and palettes, the guide and the Settings window."""
import json
import subprocess
import sys
from pathlib import Path

import matplotlib
import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QKeySequence

from echem_analyzer import help_content, palette, plots, report, theme
from echem_analyzer.analyzers import ResponseSpec, upsert_responses
from echem_analyzer.models import Curve
from echem_analyzer.prefs import DEFAULTS, SCHEMA, Prefs, clean, prefs
from echem_analyzer.study_report import _calibration_options
from helpers import make_window

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


# ===================================================================== the preference store

def test_every_default_is_valid_and_bad_values_fall_back():
    assert all(clean(k, v) == v for k, v in DEFAULTS.items())
    assert clean("plot_style", "neon") == "modern" and clean("export_dpi", 123) == 200 and clean("export_dpi", 300.0) == 300
    assert clean("plot_font_scale", 99) == 1.6 and clean("plot_font_scale", 0.1) == 0.8 and clean("plot_font_scale", "big") == 1.0
    assert clean("plot_font_scale", float("nan")) == 1.0 and clean("lod_k", True) == 3.0 and clean("recent_limit", 7.6) == 8
    assert clean("operator", "  Ada  ") == "Ada" and clean("operator", 5) == "" and len(clean("institution", "x" * 500)) == 120
    assert clean("conc_unit", "mol") == "µM" and clean("page_size", "Legal") == "A4"


def test_preferences_persist_and_survive_damage(tmp_path):
    f = tmp_path / "p.json"
    a = Prefs(f)
    seen = []
    a.on_change(lambda: seen.append(1))
    a.set("plot_style", "publication")
    a.set("plot_style", "publication")                                  # no change, no notification
    a.update({"lod_k": 3.3, "bogus": 1, "export_dpi": 999})              # unknown keys and invalid values are ignored
    assert len(seen) == 2 and json.loads(f.read_text())["plot_style"] == "publication"
    b = Prefs(f)
    assert b.get("plot_style") == "publication" and b.get("lod_k") == 3.3 and b.get("export_dpi") == 200 and b.loaded_from_disk
    f.write_text("{not json")
    c = Prefs(f)
    assert c.is_default() and (tmp_path / "p.json.corrupt").exists()
    f.write_text(json.dumps({"plot_palette": "neon", "plot_font_scale": "x", "extra": 1, "operator": "Ada"}))
    d = Prefs(f)
    assert d.get("plot_palette") == "default" and d.get("plot_font_scale") == 1.0 and d.get("operator") == "Ada"
    d.reset()
    assert d.is_default() and json.loads(f.read_text()) == DEFAULTS


def test_unwritable_settings_folder_does_not_break_the_app(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    p = Prefs(blocker / "sub" / "p.json")                              # a folder cannot be created below a file
    p.set("lod_k", 3.3)
    assert p.get("lod_k") == 3.3


def test_schema_and_signature():
    assert set(DEFAULTS) == set(SCHEMA)
    before = prefs.signature()
    prefs.set("plot_palette", "greyscale")
    assert prefs.signature() != before


def test_preferences_and_guide_are_qt_free():
    code = ("import sys; import echem_analyzer.prefs, echem_analyzer.help_content, echem_analyzer.palette, echem_analyzer.plots; "
            "assert not [m for m in sys.modules if m.startswith('PySide6')], 'Qt was imported'")
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=str(ROOT), env={"PYTHONPATH": str(ROOT)})
    assert r.returncode == 0, r.stderr


# ===================================================================== plot styles and palettes

def test_publication_style_changes_the_rc_and_the_figure():
    p = palette.LIGHT
    modern = palette.mpl_rc(p)
    assert modern["axes.spines.top"] is False and modern["axes.grid"] is True and "STIXGeneral" not in modern["font.family"]
    prefs.set("plot_style", "publication")
    pub = palette.mpl_rc(p)
    assert pub["axes.spines.top"] is True and pub["axes.grid"] is False and pub["font.family"][0] == "STIXGeneral"
    assert pub["xtick.direction"] == "in" and pub["xtick.minor.visible"] is True
    prefs.set("plot_style", "modern")
    big = prefs.set("plot_font_scale", 1.25) or palette.mpl_rc(p)
    assert big["axes.labelsize"] == pytest.approx(modern["axes.labelsize"] * 1.25) and big["lines.linewidth"] == modern["lines.linewidth"]
    a = plots.render_png(lambda fig, pal: plots.style_preview(fig, pal), (5, 3))
    prefs.set("plot_style", "publication")
    b = plots.render_png(lambda fig, pal: plots.style_preview(fig, pal), (5, 3))
    assert a != b and a[:8] == b[:8] == b"\x89PNG\r\n\x1a\n"


def test_palette_variants_change_data_colours_only():
    for variant in ("colorblind", "greyscale"):
        for base in (palette.LIGHT, palette.DARK):
            v = palette.styled(base, variant)
            assert v.series != base.series and v.trace != base.trace and len(v.series) == 8 == len(set(v.series))
            assert (v.bg, v.surface, v.accent, v.text, v.name) == (base.bg, base.surface, base.accent, base.text, base.name)
    grey = palette.styled(palette.LIGHT, "greyscale")
    assert all(c[1:3] == c[3:5] == c[5:7] for c in grey.series)             # r == g == b
    assert palette.styled(palette.LIGHT, "default") is palette.LIGHT
    prefs.set("plot_palette", "colorblind")
    assert palette.report_palette().series[0] == "#0072B2" and palette.report_palette().name == "light"


def test_export_resolution_follows_the_preference():
    draw = lambda fig, pal: plots.style_preview(fig, pal)                       # noqa: E731
    for dpi in (150, 300):
        prefs.set("export_dpi", dpi)
        png = plots.render_png(draw, (4, 3))
        assert plots.png_dpi(png) == pytest.approx(dpi, abs=1) and plots.png_size(png)[0] == 4 * dpi


def test_figure_cache_notices_a_change_of_style():
    calls = []
    make = lambda: calls.append(1) or b"x"                                      # noqa: E731
    report._cached(("k",), make)
    report._cached(("k",), make)
    assert len(calls) == 1
    prefs.set("plot_style", "publication")
    report._cached(("k",), make)
    assert len(calls) == 2


def test_study_and_response_defaults_come_from_the_preferences():
    prefs.set("lod_k", 3.3)
    prefs.set("conc_unit", "nM")
    o = _calibration_options("Peak height", "analyte", {})
    assert o.k_lod == 3.3 and o.k_loq == pytest.approx(11.0)
    assert _calibration_options("Peak height", "analyte", {"quantity": "Peak height", "k_lod": 2.0}).k_lod == 2.0   # stored wins
    c = Curve(kind="DPV", title="DPV i vs E", x=np.arange(3.0), y=np.arange(3.0), x_unit="V", y_unit="µA", meta={"run_title": "scan"}, uid="u")
    rows = []
    upsert_responses(rows, c, "peak", [ResponseSpec("Peak height", "µA", 1.0)])
    assert rows[0].conc_unit == "nM"


# ===================================================================== the guide

def test_guide_is_complete_and_consistent():
    ids = [t.id for t in help_content.TOPICS]
    assert len(ids) == len(set(ids)) and "start" in ids
    for t in help_content.TOPICS:
        assert len(t.text) > 150 and t.title and t.group
        for link in help_content.links_in(t.html):
            assert link in ids, (t.id, link)
    assert [g for g, _ in help_content.groups()][0] == "Getting started"
    assert {t.id for t in help_content.search("LOD")} >= {"calibration", "sam"}
    assert {t.id for t in help_content.search("nernst slope")} >= {"potentiometry", "glossary"}
    assert help_content.search("zzzzqqq") == [] and len(help_content.search("")) == len(ids)


def test_every_listed_shortcut_is_a_real_one(qapp, tmp_path):
    win = make_window(tmp_path / "w")
    real = {a.shortcut().toString(QKeySequence.PortableText) for a in win.findChildren(QAction) if not a.shortcut().isEmpty()}
    for keys, _what in help_content.SHORTCUTS:
        portable = keys.replace("Ctrl/⌘", "Ctrl").replace(" + ", "+").replace("⌘", "Ctrl")
        assert any(QKeySequence(portable).matches(QKeySequence(r)) == QKeySequence.ExactMatch for r in real), keys


# ===================================================================== the windows

def test_help_window_navigates_and_searches(qapp, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.show_help()
    h = win._help
    assert h.isVisible() and h.current_topic == "start" and "Quick start" in h.view.toPlainText()
    h._link(QUrl("topic:calibration"))
    assert h.current_topic == "calibration" and "LOD" in h.view.toPlainText()
    h.search.setText("nernst")
    shown = {t.id for t in help_content.search("nernst")}
    assert h.tree.currentItem().data(0, 256) in shown and "Nernst" in h.view.toPlainText()          # the open topic stays if it still matches
    h._link(QUrl("topic:start"))
    h.search.setText("nernst")
    assert h.tree.currentItem().data(0, 256) == "potentiometry"                                    # else the first match opens
    h.search.setText("zzzzqq")
    assert "No topic matches" in h.view.toPlainText()
    h.search.clear()
    win.show_help("shortcuts")                                                    # the same window, another topic
    assert win._help is h and h.current_topic == "shortcuts" and "Ctrl" in h.view.toPlainText()
    win.show_help("not-a-topic")
    assert h.current_topic == "start"


def test_settings_window_changes_everything_it_promises(qapp, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.show_settings()
    s = win._settings
    assert s.isVisible() and [s.nav.item(i).text() for i in range(s.nav.count())][0] == "Appearance"

    s.style_box.setCurrentIndex(s.style_box.findData("publication"))
    assert prefs.get("plot_style") == "publication" and matplotlib.rcParams["axes.spines.top"] is True
    ax = s.preview.fig.axes[0]
    assert ax.spines["top"].get_visible() and not ax.xaxis.get_gridlines()[0].get_visible()
    assert matplotlib.rcParams["xtick.direction"] == "in" and matplotlib.rcParams["xtick.minor.visible"] is True
    s.style_box.setCurrentIndex(s.style_box.findData("modern"))                              # and back: nothing may linger
    rc = matplotlib.rcParams
    assert (rc["xtick.direction"], rc["xtick.top"], rc["ytick.right"], rc["xtick.minor.visible"], rc["axes.spines.top"], rc["axes.grid"]) == ("out", False, False, False, False, True)
    ax = s.preview.fig.axes[0]
    assert not ax.spines["top"].get_visible() and ax.xaxis.get_gridlines()[0].get_visible() and not ax.xaxis.get_minor_ticks()
    s.style_box.setCurrentIndex(s.style_box.findData("publication"))
    s.palette_box.setCurrentIndex(s.palette_box.findData("colorblind"))
    assert theme.current().series[0] == "#0072B2" and s.preview.fig.axes[0].lines[0].get_color() == "#0072B2"
    s.font.setValue(125)
    assert prefs.get("plot_font_scale") == 1.25 and s.font_label.text() == "125 %" and matplotlib.rcParams["axes.labelsize"] > 9
    s.theme_box.setCurrentIndex(s.theme_box.findData("dark"))
    assert theme.preference() == "dark" and theme.current().name == "dark" and theme.current().series[0] == "#56B4E9"

    s.lod_k.setValue(3.3)
    s.conc_box.setCurrentIndex(s.conc_box.findData("nM"))
    s.dpi.setCurrentIndex(s.dpi.findData(300))
    s.page.setCurrentIndex(s.page.findData("Letter"))
    s.recent.setValue(3)
    s.operator.setText("Ada Lovelace")
    s.operator.editingFinished.emit()
    s.institution.setText("Rice University")
    s.institution.editingFinished.emit()
    assert (prefs.get("lod_k"), prefs.get("conc_unit"), prefs.get("export_dpi"), prefs.get("page_size"), prefs.get("recent_limit")) == (3.3, "nM", 300, "Letter", 3)
    assert prefs.get("operator") == "Ada Lovelace" and Prefs(prefs.path).get("institution") == "Rice University"     # saved to disk

    assert win.new_project() and win.session.meta.operator == "Ada Lovelace"                  # used for new projects
    win.stack.setCurrentIndex(0)
    win.welcome.set_recents(["/a/1.echemproj", "/a/2.echemproj", "/a/3.echemproj", "/a/4.echemproj"])
    assert win.welcome.recent_box.count() == 3

    s.theme_box.setCurrentIndex(s.theme_box.findData("light"))
    s.close()
    win.show_settings()                                                                       # reopening shows the saved values
    assert s.lod_k.value() == 3.3 and s.dpi.currentData() == 300 and s.operator.text() == "Ada Lovelace"


def test_reset_restores_defaults(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    win = make_window(tmp_path / "w")
    win.show_settings()
    s = win._settings
    prefs.update({"plot_style": "publication", "lod_k": 3.3, "operator": "X"})
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    s._reset()
    assert prefs.get("lod_k") == 3.3                                                          # declined
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    s._reset()
    assert prefs.is_default() and matplotlib.rcParams["axes.spines.top"] is False and s.lod_k.value() == 3.0 and s.operator.text() == ""


def test_welcome_tiles_open_the_guide_and_tabs_have_icons(qapp, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    tiles = win.welcome.findChildren(type(win.welcome.findChild(object, "Tile")))
    tiles = [t for t in win.welcome.findChildren(__import__("PySide6.QtWidgets", fromlist=["QPushButton"]).QPushButton) if t.objectName() == "Tile"]
    assert len(tiles) == 6
    tiles[0].click()
    assert win._help.isVisible() and win._help.current_topic == "calibration"
    tiles[2].click()
    assert win._help.current_topic == "eis"
    assert all(not win.tabs.tabIcon(i).isNull() for i in range(win.tabs.count()))
    assert not win.tb_help.icon().isNull() and not win.tb_settings.icon().isNull()
    win.tb_settings.trigger()
    assert win._settings.isVisible()


def test_reports_follow_the_plot_style(qapp, tmp_path):
    """A report built after the user switches to publication style has different figures and the institution on the cover."""
    from test_study_report import peak_responses                                              # synthetic DPV series
    from echem_analyzer.analyzers import ANALYZERS
    from echem_analyzer.models import ProjectData, ProjectMeta
    df = peak_responses(tmp_path, "E1", 0.8, 1)
    responses = []
    an = ANALYZERS["peak"]
    for c in df.curves[1:]:
        upsert_responses(responses, c, "peak", [s for s in an.run(c, an.defaults()).responses if s.quantity == "Peak height"])
    data = ProjectData(meta=ProjectMeta(name="Style"), files=[df], responses=responses)
    a = report.build_report(data)
    prefs.update({"plot_style": "publication", "plot_palette": "greyscale", "institution": "Rice University", "operator": "Ada"})
    b = report.build_report(data)
    fa, fb = a.figures(), b.figures()
    assert set(fa) == set(fb) and all(fa[k].png != fb[k].png for k in fa)
    assert ("Institution", "Rice University") in b.meta_rows and ("Operator", "Ada") in b.meta_rows
    assert not any(k == "Institution" for k, _ in a.meta_rows)
