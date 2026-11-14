"""The device registry: variants, lots and fabrication parameters of the electrodes a study is about."""
import csv
import io

import pytest

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
