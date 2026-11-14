"""The bench calculators window: inputs in, plain-language results out, and its place in the Tools menu."""
import pytest
from PySide6.QtGui import QGuiApplication, QKeySequence

import synth
from echem_analyzer.gui import bench_dialog
from echem_analyzer.gui.bench_dialog import BenchDialog, parse_list, parse_number
from echem_analyzer.bench import BenchError
from helpers import make_window


@pytest.fixture()
def dlg(qapp):
    return BenchDialog()


def text(page) -> str:
    return page.result.text().replace("<b>", "").replace("</b>", "").replace("<br>", "\n")


def set_all(page, **fields):
    for name, value in fields.items():
        getattr(page, name).setText(value)


# ===================================================================== parsing

def test_numbers_accept_decimal_commas_and_say_what_is_wrong():
    assert parse_number("2e-6", "x") == 2e-6 and parse_number(" 1,5 ", "x") == 1.5
    assert parse_number("", "x", required=False) is None
    with pytest.raises(BenchError, match="Enter the volume"):
        parse_number(" ", "the volume")
    with pytest.raises(BenchError, match="not a number: “abc”"):
        parse_number("abc", "the volume")
    with pytest.raises(BenchError):
        parse_number("inf", "the volume")


def test_lists_split_on_commas_semicolons_or_spaces():
    assert parse_list("10, 20, 50,", "t") == [10, 20, 50] and parse_list("10,20,50", "t") == [10, 20, 50]
    assert parse_list("0,5; 1,5", "t") == [0.5, 1.5] and parse_list("1 2   3", "t") == [1, 2, 3]
    with pytest.raises(BenchError):
        parse_list("1, x", "t")
    with pytest.raises(BenchError):
        parse_list("", "t")


# ===================================================================== the pages

def test_every_page_opens_with_something_useful_to_say(dlg):
    assert list(dlg.pages) == ["Dilution", "Serial dilution", "Standard addition", "Expected signal", "Nernst slope", "Ionic strength", "Weighing"]
    for title, page in dlg.pages.items():
        assert not page.result.isHidden() and page.result.text(), title
        assert "is not a number" not in page.result.text() and "must be" not in page.result.text(), (title, page.result.text())


def test_dilution_solves_whichever_box_is_left_empty(dlg):
    page = dlg.pages["Dilution"]
    set_all(page, c1="10", c2="0.1", v2="50")
    assert "Take 0.5 mL" in text(page) and "add 49.5 mL of solvent" in text(page) and "100× dilution" in text(page)
    set_all(page, v1="0.5", c2="")                                                     # now the final concentration is the unknown
    assert "0.1 mM" in text(page)
    set_all(page, c2="0.1")                                                            # all four filled
    assert "Fill in three of the four" in text(page)
    set_all(page, v2="abc")
    assert "not a number" in text(page)
    set_all(page, c1="1", v1="1", c2="5", v2="")                                       # c2 > c1
    assert "not a dilution" in text(page)


def test_serial_dilution_fills_a_table(dlg):
    page = dlg.pages["Serial dilution"]
    assert len(page.table_rows()) == 6 and page.table_rows()[0] == ["1", "1 mM", "100 µL", "900 µL"]
    page.tubes.setValue(3)
    assert len(page.table_rows()) == 3
    page.factor.setText("1")
    assert "greater than 1" in text(page) and page.table.rowCount() == 0              # an impossible input clears the old table


def test_standard_addition_plan_table_warnings_and_clipboard(dlg):
    page = dlg.pages["Standard addition"]
    rows = page.table_rows()
    assert [r[1] for r in rows] == ["10 µM", "20 µM", "50 µM", "100 µM"]
    assert rows[0][2] == "10.00"                                                       # 10.01 µL, rounded to the pipette's 0.1 µL
    assert "Total" in text(page) and "keeps the additions under 5 %" in text(page) and "⚠" not in text(page)
    set_all(page, stock="100000", targets="1, 5")
    assert "below the 0.5 µL" in text(page) and page.result.property("tone") == "warning"
    set_all(page, stock="10000", targets="10, 20, 50, 100")
    page.copy_table()
    clip = QGuiApplication.clipboard().text()
    assert clip.splitlines()[0] == "Step\tTarget\tAdd (µL)\tTotal (µL)\tReached\tLeft" and len(clip.splitlines()) == 5
    page.targets.setText("5, 3")
    assert "must increase" in text(page)


def test_expected_signal_for_the_typical_ferricyanide_electrode(dlg):
    page = dlg.pages["Expected signal"]
    page.rate.setText("200")
    page.btn.click()                                                                   # resets to the typical values
    assert page.rate.text() == "100" and "16.5" in text(page) and "100 µA" in text(page)
    assert len(page.table_rows()) == 7 and page.table_rows()[3][0] == "100 mV/s"
    page.alpha.setText("0.5")                                                          # irreversible: a smaller peak
    assert "16.5" not in text(page)
    page.area.setText("-1")
    assert "positive" in text(page)


def test_nernst_slope_for_a_cation_and_an_anion(dlg):
    page = dlg.pages["Nernst slope"]
    assert "+59.16 mV/decade" in text(page) and "rises by 118.3 mV" in text(page)
    page.z.setValue(-1)
    assert "-59.16 mV/decade" in text(page) and "falls by 118.3 mV" in text(page)
    page.z.setValue(0)
    assert "non-zero whole number" in text(page)


def test_ionic_strength_of_a_salt_a_buffer_and_nothing(dlg):
    page = dlg.pages["Ionic strength"]
    assert "100 mM" in text(page) and "0.9608 nm" in text(page)
    page.load_pbs()
    assert page.salts.rowCount() == 4 and "171.5 mM" in text(page)                     # phosphate treated as fully dissociated
    page.salts.selectAll()
    page.remove_selected()
    assert "Add at least one salt" in text(page)
    page.add_salt("Na₂SO₄", "10", 1, 2)
    assert "30 mM" in text(page)                                                       # 3c for a 1:2 salt
    page.salts.item(0, 1).setText("ten")
    assert "row 1" in text(page) and "not a number" in text(page)


def test_weighing(dlg):
    page = dlg.pages["Weighing"]
    assert "164.6 mg" in text(page)
    set_all(page, mw="")
    assert "Enter the molar mass" in text(page)


# ===================================================================== in the main window

def test_the_tools_menu_opens_one_reusable_window(qapp, tmp_path):
    win = make_window(tmp_path / "w")
    menus = {a.text().replace("&", ""): a.menu() for a in win.menuBar().actions()}
    assert list(menus) == ["File", "View", "Tools", "Help"]
    assert [a.text() for a in menus["Tools"].actions()] == ["Bench calculators…", "Review import checks"]
    assert win.act_bench.shortcut() == QKeySequence("Ctrl+Shift+B")
    win.act_bench.trigger()
    first = win._bench
    assert first.isVisible() and first.tabs.currentWidget() is first.pages["Dilution"]
    first.pages["Dilution"].c1.setText("42")
    win.show_bench("Weighing")
    assert win._bench is first and first.tabs.currentWidget() is first.pages["Weighing"]
    assert first.pages["Dilution"].c1.text() == "42"                                   # what was typed is still there
    first.close()


def test_review_import_checks_goes_to_the_measurements_table(qapp, tmp_path):
    import numpy as np
    win = make_window(tmp_path / "w")
    win.act_checks.trigger()
    assert "Nothing is loaded" in win.statusBar().currentMessage()
    t = np.linspace(0, 10, 51)
    synth.write_pstrace_csv(tmp_path / "a.csv", [("CA: i vs t", t, 1 + 0 * t + 0.01 * np.sin(5 * t), "s", "µA")])
    win.load_paths([tmp_path / "a.csv"])
    win.tabs.setCurrentWidget(win.ca_tab)
    win.act_checks.trigger()
    assert win.tabs.currentWidget() is win.measurements_tab


def test_every_unit_offered_is_one_the_calculators_understand():
    from echem_analyzer.study_analysis import CONC_FACTORS
    assert all(u in CONC_FACTORS for u in bench_dialog.CONC_UNITS)
