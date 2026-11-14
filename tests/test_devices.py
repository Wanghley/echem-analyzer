"""The device registry: variants, lots and fabrication parameters of the electrodes a study is about."""
import csv
import io

import pytest
from PySide6.QtCore import Qt

from echem_analyzer import devices as dv
from echem_analyzer.devices import Device
from echem_analyzer.models import Response


def registry():
    return {d.id: d for d in (
        Device("E1", "thin ink", "L1", "2026-11-02", "", {"ink volume (µL)": 2.0, "anneal": "120 °C"}),
        Device("E2", "thin ink", "L1", "2026-11-02", "edge crack", {"ink volume (µL)": 2.0}),
        Device("E3", "thick ink", "L2", "2026-11-09", "", {"ink volume (µL)": 5.0, "anneal": "120 °C"}),
    )}


def test_parameters_are_numbers_when_they_are_numbers():
    assert dv.parse_value("2.5") == 2.5 and dv.parse_value("2,5") == 2.5 and dv.parse_value(" 1e-3 ") == 1e-3
    assert dv.parse_value("120 °C") == "120 °C" and dv.parse_value("") == "" and dv.parse_value("   ") == ""
    assert dv.parse_value(3) == 3.0 and dv.parse_value(float("nan")) == "" and dv.parse_value("nan") == "nan"
    assert dv.is_number(2.0) and not dv.is_number(True) and not dv.is_number("2") and not dv.is_number(float("inf"))


def test_the_registry_round_trips_through_a_project_and_ignores_junk():
    extras: dict = {}
    dv.save(extras, registry())
    assert [d["id"] for d in extras["devices"]] == ["E1", "E2", "E3"]
    back = dv.load(extras)
    assert back["E3"].params == {"ink volume (µL)": 5.0, "anneal": "120 °C"} and back["E2"].notes == "edge crack"
    messy = {"devices": [{"id": " E1 ", "variant": "a"}, {"id": ""}, "nonsense", None, {"id": "E1", "variant": "second"},
                         {"id": "E9", "params": "not a dict", "lot": None}]}
    got = dv.load(messy)
    assert list(got) == ["E1", "E9"] and got["E1"].variant == "a" and got["E9"].lot == "" and got["E9"].params == {}
    assert dv.load({}) == {} and dv.load({"devices": None}) == {}


def test_every_electrode_in_the_responses_gets_a_device_without_losing_notes():
    rs = [Response(id=f"r{i}", group=g) for i, g in enumerate(["E1", "E4", "", "E4", "E5"])]
    synced = dv.sync(registry(), rs)
    assert list(synced) == ["E1", "E2", "E3", "E4", "E5"]                               # E2 and E3 stay although no response names them
    assert synced["E2"].notes == "edge crack" and synced["E4"] == Device("E4")
    assert dv.sync({}, []) == {}


def test_variants_lots_and_the_parameters_a_trend_can_use():
    reg = registry()
    assert dv.variants(reg) == ["thick ink", "thin ink"] and dv.lots(reg) == ["L1", "L2"]
    assert dv.param_names(reg) == ["ink volume (µL)", "anneal"]
    assert dv.numeric_params(reg) == ["ink volume (µL)"]                                 # "anneal" is text; one device has no value for it
    assert [d.id for d in dv.of_variant(reg, "thin ink")] == ["E1", "E2"]
    constant = {"a": Device("a", params={"x": 1.0}), "b": Device("b", params={"x": 1.0})}
    assert dv.numeric_params(constant) == []                                             # nothing varies: nothing to plot against


def test_csv_round_trip_keeps_the_text_the_numbers_and_the_blanks():
    text = dv.to_csv(registry())
    assert text.splitlines()[0] == "id,variant,lot,fabricated,notes,ink volume (µL),anneal"
    assert text.splitlines()[2] == "E2,thin ink,L1,2026-11-02,edge crack,2,"
    back = {d.id: d for d in dv.from_csv(text)}
    assert back["E1"].params == {"ink volume (µL)": 2.0, "anneal": "120 °C"} and back["E2"].params == {"ink volume (µL)": 2.0}
    assert back["E3"].variant == "thick ink" and back["E2"].notes == "edge crack"


@pytest.mark.parametrize("delim", [",", ";", "\t"])
def test_csv_import_accepts_any_delimiter_any_column_order_and_decimal_commas(delim):
    rows = [["Lot", "ID", "Variant", "Deposition time (s)", "Solvent"], ["L7", "S1", "dense", "30,5", "water"], ["L7", "S2", "dense", "45", ""],
            ["", "", "ignored", "1", ""], ["L8", "S1", "duplicate id", "99", ""]]
    buf = io.StringIO()
    csv.writer(buf, delimiter=delim, lineterminator="\n").writerows(rows)          # a decimal comma in a comma file is quoted, as a real export does
    text = "\ufeff" + buf.getvalue()
    devs = {d.id: d for d in dv.from_csv(text)}
    assert list(devs) == ["S1", "S2"] and devs["S1"].lot == "L7" and devs["S1"].variant == "dense"      # the repeated id keeps its first row
    assert devs["S1"].params == {"Deposition time (s)": 30.5, "Solvent": "water"} and devs["S2"].params == {"Deposition time (s)": 45.0}


def test_csv_without_an_id_column_is_refused_with_a_reason_and_empty_input_is_empty():
    with pytest.raises(ValueError, match="called id"):
        dv.from_csv("name,variant\nE1,a\n")
    assert dv.from_csv("") == [] and dv.from_csv("   \n") == []


def test_importing_into_a_registry_fills_fields_without_erasing_the_rest():
    reg = registry()
    incoming = dv.from_csv("id,variant,lot,anneal,thickness\nE1,medium ink,,150 °C,1.5\nE7,new,L9,,\n")
    merged = dv.merge(reg, incoming)
    assert merged["E1"].variant == "medium ink" and merged["E1"].lot == "L1"             # a blank in the file keeps what was there
    assert merged["E1"].params == {"ink volume (µL)": 2.0, "anneal": "150 °C", "thickness": 1.5}
    assert merged["E7"].lot == "L9" and list(merged) == ["E1", "E2", "E3", "E7"]
    assert reg["E1"].variant == "thin ink" and "thickness" not in reg["E1"].params       # the registry it was given is untouched (a cancelled import)
    assert "E7" not in reg


# ===================================================================== the Devices tab

def screen_responses():
    rs = []
    for dev, dep, ratio in (("E1", 70, 1.02), ("E2", 95, 1.0), ("E3", 60, None), ("E4", 85, 1.3)):
        rs.append(Response(id=f"{dev}|d", group=dev, quantity="ΔEp", unit="mV", value=float(dep)))
        if ratio is not None:
            rs.append(Response(id=f"{dev}|r", group=dev, quantity="ip ratio", unit="", value=ratio))
    return rs


def make_panel(qapp, tmp_path):
    from helpers import make_window
    win = make_window(tmp_path / "w")
    win.session.responses[:] = screen_responses()
    win.session.responses_changed.emit()
    return win, win.study_tab.devices


def test_the_devices_tab_lists_every_electrode_and_stores_what_is_typed(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    win, panel = make_panel(qapp, tmp_path)
    st = win.study_tab
    assert st.tabs.tabText(st.tabs.count() - 2) == "Devices" and st.tabs.tabText(st.tabs.count() - 1) == "Variants"      # appended, in this order
    assert [panel.table.item(r, 0).text() for r in range(panel.table.rowCount())] == ["E1", "E2", "E3", "E4"]
    panel.table.item(0, 1).setText("thin ink")
    panel.table.item(1, 1).setText("thin ink")
    panel.table.item(2, 2).setText("L2")
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("ink volume (µL)", True)))
    panel.add_parameter()
    col = [panel.table.horizontalHeaderItem(c).text() for c in range(panel.table.columnCount())].index("ink volume (µL)")
    panel.table.item(0, col).setText("2,5")                                                  # a decimal comma
    saved = {d["id"]: d for d in win.session.extras["devices"]}
    assert saved["E1"]["variant"] == "thin ink" and saved["E3"]["lot"] == "L2" and saved["E1"]["params"]["ink volume (µL)"] == 2.5
    assert saved["E2"]["params"]["ink volume (µL)"] == "" and win.session.dirty
    assert not (panel.table.item(0, 0).flags() & Qt.ItemIsEditable)                            # the electrode name is not editable here
    assert panel.table.item(0, 1).flags() & Qt.ItemIsEditable
    win.session.responses_changed.emit()                                                      # a rebuild keeps everything
    assert panel.table.item(0, 1).text() == "thin ink" and panel.table.item(0, col).text() == "2.5"


def test_loading_a_template_gives_each_device_a_verdict_and_the_batch_a_yield(qapp, tmp_path):
    win, panel = make_panel(qapp, tmp_path)
    panel.table.item(0, 1).setText("thin")
    panel.table.item(1, 1).setText("thin")
    panel.table.item(2, 1).setText("thick")
    panel.table.item(3, 1).setText("thick")
    assert panel.rules.rowCount() == 0 and panel.yield_table.rowCount() == 0
    panel._load_selected_set(1)                                                               # the ferri/ferrocyanide template
    assert [panel.rules.item(r, 0).text() for r in range(2)] == ["ΔEp", "ip ratio"]
    last = panel.table.columnCount() - 1
    verdict = {panel.table.item(r, 0).text(): panel.table.item(r, last).text() for r in range(4)}
    assert verdict["E1"] == "pass" and verdict["E3"].startswith("not measured:")
    assert verdict["E2"].startswith("fail:") and "(got 95)" in verdict["E2"] and verdict["E4"].count("(got") == 2
    rows = {panel.yield_table.item(r, 0).text(): [panel.yield_table.item(r, c).text() for c in range(1, 7)] for r in range(panel.yield_table.rowCount())}
    assert rows["thin"][:4] == ["2", "1", "1", "0"] and rows["thin"][4] == "50 %"
    assert rows["thick"][:4] == ["2", "0", "1", "1"] and rows["All devices"][1:3] == ["1", "2"]
    panel.by.setCurrentIndex(1)                                                               # by lot: none set
    assert [panel.yield_table.item(r, 0).text() for r in range(panel.yield_table.rowCount())] == ["(not set)", "All devices"]


def test_rules_can_be_edited_removed_saved_and_loaded_again(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    win, panel = make_panel(qapp, tmp_path)
    panel.add_rule()
    panel.rules.item(0, 0).setText("ΔEp")
    panel.rules.item(0, 2).setText("80")
    panel.rules.item(0, 5).setText("EACH")
    panel.rules.item(0, 1).setText("not a number")                                            # ignored, not fatal
    assert win.session.extras["qc_criteria"] == [{"quantity": "ΔEp", "minimum": None, "maximum": 80.0, "condition": "", "role": "",
                                                  "how": "each", "name": ""}]
    last = panel.table.columnCount() - 1
    assert panel.table.item(1, last).text().startswith("fail") and panel.table.item(0, last).text() == "pass"
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("My screen", True)))
    panel.save_set()
    assert panel.sets.findText("Saved: My screen") > 0
    panel.rules.selectRow(0)
    panel.remove_rule()
    assert panel.rules.rowCount() == 0 and win.session.extras["qc_criteria"] == []
    panel._load_selected_set(panel.sets.findText("Saved: My screen"))
    assert panel.rules.rowCount() == 1 and win.session.extras["qc_criteria"][0]["how"] == "each"


def test_devices_import_and_export_through_the_file_dialogs(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    win, panel = make_panel(qapp, tmp_path)
    src = tmp_path / "lab.csv"
    src.write_text("id;variant;lot;anneal\nE1;thin;L1;120 °C\nE9;extra;L3;\n", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(src), "")))
    panel.import_csv()
    assert [panel.table.item(r, 0).text() for r in range(panel.table.rowCount())] == ["E1", "E2", "E3", "E4", "E9"]
    assert panel.table.item(0, 1).text() == "thin" and panel.table.horizontalHeaderItem(5).text() == "anneal"
    out = tmp_path / "out.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    panel.export_csv()
    assert out.read_text(encoding="utf-8").splitlines()[0] == "id,variant,lot,fabricated,notes,anneal"
    bad = tmp_path / "bad.csv"
    bad.write_text("name,variant\nE1,x\n", encoding="utf-8")
    warned = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(bad), "")))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a: warned.append(a[-1])))
    panel.import_csv()
    assert warned and "called id" in warned[0] and panel.table.rowCount() == 5                # nothing changed


def test_devices_and_rules_survive_saving_the_project(qapp, tmp_path):
    from echem_analyzer.project import ProjectData, load_project, save_project
    win, panel = make_panel(qapp, tmp_path)
    panel.table.item(0, 1).setText("thin")
    panel._load_selected_set(1)
    save_project(tmp_path / "p.echemproj", ProjectData(responses=list(win.session.responses), extras=win.session.extras))
    back = load_project(tmp_path / "p.echemproj")
    assert dv.load(back.extras)["E1"].variant == "thin" and len(back.extras["qc_criteria"]) == 2
