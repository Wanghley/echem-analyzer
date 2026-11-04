"""Application shell: welcome page, theming, headline tiles, export actions, compact widgets."""
import re
from pathlib import Path

import openpyxl
import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from echem_analyzer import palette, theme
from echem_analyzer.gui import main_window as mw
from echem_analyzer.gui.ui_kit import CollapsibleCard
from echem_analyzer.project import SUFFIX
from helpers import fill_additions, find_ca, find_cv_run, make_window, shot
from PySide6.QtCore import Qt

B2_CSV, B2_PS = "2026-11-02 B2 GY data.csv", "2026-11-02 B2 GY data.pssession"


@pytest.fixture(autouse=True)
def _restore_light_theme(qapp):
    yield
    theme.apply(qapp, "light")


# ===================================================================== welcome page / workspace

def test_welcome_page_until_data_is_loaded(qapp, workdir, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    assert win.stack.currentIndex() == mw.PAGE_WELCOME and not win.files_dock.isVisible()
    assert not win.act_pdf.isEnabled() and not win.export_btn.isEnabled()
    assert "0 files" in win.count_label.text()
    shot(win, qapp, "9_welcome")

    win.load_paths([workdir])
    assert win.stack.currentIndex() == mw.PAGE_WORK and win.files_dock.isVisible()
    assert win.act_pdf.isEnabled() and win.act_xlsx.isEnabled() and win.export_btn.isEnabled()
    assert "4 files" in win.count_label.text() and "6 CA runs" in win.count_label.text()

    win.session.dirty = False
    win.clear_data = lambda: win.session.clear()                      # skip the confirmation dialog
    win.clear_data()
    assert win.stack.currentIndex() == mw.PAGE_WELCOME


def test_loading_behind_the_welcome_page_draws_every_plot(qapp, workdir, tmp_path):
    """Regression: the tabs are hidden while the welcome page shows, so their canvases are 0x0; matplotlib
    then failed with 'Singular matrix' on the first draw. (The autouse fixture also fails on slot errors.)"""
    win = make_window(tmp_path / "w")
    win.show()
    assert win.stack.currentIndex() == mw.PAGE_WELCOME
    win.load_paths([workdir])
    assert win.ca_tab.ax_ca.lines and win.ca_tab.result is not None
    assert win.cv_tab.cycles.ax_cv.lines and win.cv_tab.cycles.results
    assert win.compare_tab.ax_ca.lines
    for _ in range(6):
        qapp.processEvents()
    assert win.ca_tab.ca_plot.canvas.width() > 300                    # a real size once it is shown


def test_recent_projects_appear_on_the_welcome_page(qapp, workdir, tmp_path):
    a = make_window(tmp_path / "a")
    a.load_paths([workdir / B2_CSV])
    proj = tmp_path / f"recent{SUFFIX}"
    a.save_project_to(proj)
    b = make_window(tmp_path / "a")                                    # same settings -> same recents
    assert b.welcome.recent_card.isVisibleTo(b.welcome) and b.welcome.recent_box.count() == 1
    assert b.welcome.recent_box.itemAt(0).widget().toolTip() == str(proj)


def test_toolbar_shows_project_name_and_unsaved_marker(qapp, workdir, tmp_path):
    win = make_window(tmp_path / "w")
    assert win.name_label.text() == "Untitled" and win.sub_label.text() == "Unsaved project"
    win.load_paths([workdir / B2_CSV])
    assert win.name_label.text().endswith("●")
    proj = tmp_path / f"named{SUFFIX}"
    win.save_project_to(proj)
    assert win.name_label.text() == "named" and win.sub_label.text() == str(proj)


# ===================================================================== theming

def test_switching_theme_restyles_window_and_plots(qapp, workdir, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([workdir])
    ca = win.ca_tab
    ca.select_curve(find_ca(win, B2_CSV).uid)
    assert theme.current().name == "light" and ca.ax_ca.get_facecolor()[:3] == pytest.approx(
        tuple(int(palette.LIGHT.plot_bg[i:i + 2], 16) / 255 for i in (1, 3, 5)))

    win.set_theme("dark")
    assert theme.current().name == "dark" and str(win.session.qs.value("theme")) == "dark"
    assert palette.DARK.surface.lower() in qapp.styleSheet().lower()
    dark_rgb = tuple(int(palette.DARK.plot_bg[i:i + 2], 16) / 255 for i in (1, 3, 5))
    assert ca.ax_ca.get_facecolor()[:3] == pytest.approx(dark_rgb)                      # axes were redrawn
    assert ca.ca_plot.fig.get_facecolor()[:3] == pytest.approx(dark_rgb)                # and the figure background
    assert win.cv_tab.cycles.ax_cv.get_facecolor()[:3] == pytest.approx(dark_rgb)
    rgb = lambda hex_: tuple(int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))               # noqa: E731
    from matplotlib.colors import to_rgb
    for ax in (ca.ax_ca, win.cv_tab.cycles.ax_cv, win.compare_tab.ax_ca):                   # cleared axes, not new ones
        assert to_rgb(ax.spines["left"].get_edgecolor()) == pytest.approx(rgb(palette.DARK.border_strong))
        assert to_rgb(ax.get_title(loc="left") and ax._left_title.get_color()) == pytest.approx(rgb(palette.DARK.text))
        assert to_rgb(ax.xaxis.get_ticklabels()[0].get_color()) == pytest.approx(rgb(palette.DARK.muted))
        assert to_rgb(ax.xaxis.label.get_color()) == pytest.approx(rgb(palette.DARK.muted))
    assert win.theme_actions["dark"].isChecked()
    shot(win, qapp, "10_dark")

    win.toggle_theme()
    assert theme.current().name == "light" and win.theme_actions["light"].isChecked()
    assert ca.ax_ca.get_facecolor()[:3] == pytest.approx(rgb(palette.LIGHT.plot_bg))        # and back again
    assert to_rgb(ca.ax_ca._left_title.get_color()) == pytest.approx(rgb(palette.LIGHT.text))


def test_theme_preference_is_remembered_and_reapplying_is_free(qapp, tmp_path):
    a = make_window(tmp_path / "w")
    a.set_theme("dark")
    b = make_window(tmp_path / "w")                                                     # a new window, same settings
    assert theme.current().name == "dark"
    seen = []
    theme.bus.changed.connect(lambda: seen.append(1))
    theme.apply(qapp, "dark")                                                           # nothing changes: no work, no redraws
    assert seen == []
    theme.apply(qapp, "light")
    assert seen == [1] and theme.current().name == "light"
    b.set_theme("system")
    assert theme.preference() == "system"


def test_every_palette_defines_every_token_with_valid_colours():
    from PySide6.QtGui import QColor
    for pal in (palette.LIGHT, palette.DARK):
        for name, value in pal.__dict__.items():
            if name in ("name", "series"):
                continue
            assert QColor(value).isValid(), (pal.name, name, value)
        assert all(QColor(c).isValid() for c in pal.series) and len(pal.series) >= 6
    assert set(palette.LIGHT.__dict__) == set(palette.DARK.__dict__)


# ===================================================================== headline tiles

def test_ca_tiles_follow_the_fit(qapp, workdir, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([workdir])
    ca = win.ca_tab
    ca.select_curve(find_ca(win, B2_CSV).uid)
    assert ca.kpi_sens.value.text() == "—" and "concentrations" in ca.kpi_sens.sub.text()
    fill_additions(ca)
    assert ca.kpi_sens.value.text() == "0.665" and ca.kpi_cx.value.text() == "0.5968"
    assert ca.kpi_r2.value.text() == "0.9883" and "6 points" in ca.kpi_r2.sub.text()
    r = ca.result                                                       # default σ is s(y/x) and LOD = 3.3σ/S since the module rewrite
    assert r.sigma_used == "fit" and ca.kpi_lod.value.text() == "0.377" and "LOQ" in ca.kpi_lod.sub.text()
    assert r.lod == pytest.approx(3.3 * r.fit.s_yx / r.sensitivity)
    assert r.lods["baseline"] < r.lods["fit"] / 50                       # this run scatters far more between plateaus than its noise
    assert any("LOD from baseline noise" in w for w in r.warnings) and "LOD from baseline noise" in ca.results_label.text()


def test_cv_tiles_report_conditioning(qapp, workdir, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([workdir])
    cyc = win.cv_tab.cycles
    cyc.select_run(find_cv_run(win, B2_PS).uid)
    assert cyc.kpi_cycles.value.text() == "5 / 5"
    assert cyc.kpi_status.value.text() == "Settling" and cyc.kpi_change.value.text() == "2.38 %"
    assert "anodic" in cyc.kpi_status.sub.text()
    cyc.table.item(0, 0).setCheckState(Qt.Unchecked)
    assert cyc.kpi_cycles.value.text() == "4 / 5"
    cyc.tol.setValue(5.0)                                   # a looser tolerance: the same data now counts as settled
    assert cyc.kpi_status.value.text() == "Stable"


# ===================================================================== exports

def test_export_actions_write_real_files(qapp, workdir, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([workdir])
    fill_additions(win.ca_tab)
    win.session.set_meta(__import__("echem_analyzer.models", fromlist=["x"]).ProjectMeta(name="Export test"))

    pdf = tmp_path / "r.pdf"
    pages = win.export_report_pdf(pdf)
    assert pdf.read_bytes()[:5] == b"%PDF-" and pages >= 5
    xlsx = tmp_path / "w.xlsx"
    sheets = win.export_workbook(xlsx)
    wb = openpyxl.load_workbook(xlsx)
    assert wb.sheetnames == sheets and wb["Summary"]["A1"].value == "Export test"


def test_export_dialogs_add_the_extension_and_report_done(qapp, workdir, tmp_path, monkeypatch):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([workdir])
    done = []
    monkeypatch.setattr(win, "_export_done", lambda title, msg, path: done.append((title, path)))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(tmp_path / "noext"), "")))
    win.export_report_dialog()
    win.export_workbook_dialog()
    assert (tmp_path / "noext.pdf").is_file() and (tmp_path / "noext.xlsx").is_file()
    assert [d[0] for d in done] == ["Report exported", "Workbook exported"]


def test_nothing_to_export_is_explained_not_silent(qapp, tmp_path, monkeypatch):
    win = make_window(tmp_path / "w")
    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[1])))
    assert win._pick_export_path("Export report", ".pdf", "PDF (*.pdf)") is None
    assert shown == ["Nothing to export yet"]


# ===================================================================== compact widgets

def test_advanced_settings_start_folded(qapp, workdir, tmp_path):
    win = make_window(tmp_path / "w")
    win.show()
    win.load_paths([workdir])
    cards = win.ca_tab.panel.findChildren(CollapsibleCard)
    assert len(cards) == 1 and not cards[0].is_expanded() and not cards[0].content.isVisible()
    cards[0].toggle.click()
    assert cards[0].is_expanded() and cards[0].content.isVisible() and cards[0].toggle.text().startswith("▾")
    # the controls inside still work while folded or not
    win.ca_tab._binds["drop_last"].setValue(2)
    assert win.ca_tab.s.drop_last == 2


def test_plot_toolbar_is_compact(qapp, workdir, tmp_path):
    win = make_window(tmp_path / "w")
    labels = [a.text() for a in win.ca_tab.ca_plot.toolbar.actions() if a.text()]
    assert labels == ["Home", "Back", "Forward", "Pan", "Zoom", "Save"]
    assert "Subplots" not in labels and "Customize" not in labels
