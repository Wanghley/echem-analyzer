"""Loading other techniques (synthetic files in the PalmSens layouts) and importing arbitrary tables."""
import numpy as np
import openpyxl
import pytest

import synth
from echem_analyzer import importers, techniques
from echem_analyzer.io_palmsens import load_csv, load_pssession
from echem_analyzer.models import Curve, DataFile, ProjectData, ProjectMeta, Response
from echem_analyzer.project import load_project, save_project


# ===================================================================== PSTrace CSV / pssession with other techniques

def test_csv_with_several_techniques_is_classified(tmp_path):
    e, i = synth.dpv_trace(10.0)
    t = np.linspace(0, 30, 301)
    synth.write_pstrace_csv(tmp_path / "mix.csv", [
        ("Differential Pulse Voltammetry: DPV i vs E", e, i, "V", "µA"),
        ("Linear Sweep Voltammetry: LSV i vs E", e, i, "V", "µA"),
        ("Open Circuit Potential: OCP E vs t", t, 0.2 + 0.001 * t, "s", "V"),
        ("Chronocoulometry: CC Q vs t", t, 3 * np.sqrt(t), "s", "µC"),
        ("Square Wave Voltammetry: SWV i vs E", e, i, "V", "µA"),
    ])
    f = load_csv(tmp_path / "mix.csv")
    assert [c.kind for c in f.curves] == ["DPV", "LSV", "OCP", "CC", "SWV"]
    assert np.allclose(f.curves[0].y, i) and f.curves[2].y_unit == "V"


def test_pssession_methods_set_the_technique_and_a_title_cannot_mislead_it(tmp_path):
    e, i = synth.dpv_trace(5.0)
    t = np.linspace(0, 10, 101)
    synth.write_pssession(tmp_path / "s.pssession", [
        {"title": "sensor B7 10 µM", "method_id": "dpv", "curves": [("DPV i vs E", e, i, "PalmSens.Units.Volt", "PalmSens.Units.MicroAmpere")]},
        {"title": "blank", "method_id": "swv", "curves": [("net", e, i, "PalmSens.Units.Volt", "PalmSens.Units.MicroAmpere")]},
        {"title": "drift test", "method_id": "ocp", "curves": [("E vs t", t, 0.1 + 0 * t, "PalmSens.Units.Second", "PalmSens.Units.Volt")]},
        {"title": "step", "method_id": "ad", "curves": [("i vs t", t, 1 + 0 * t, "PalmSens.Units.Second", "PalmSens.Units.MicroAmpere")]},
    ])
    f = load_pssession(tmp_path / "s.pssession")
    assert [(c.meta["run_title"], c.kind) for c in f.curves] == [
        ("sensor B7 10 µM", "DPV"), ("blank", "SWV"), ("drift test", "OCP"), ("step", "CA")]


def test_eis_is_read_from_the_eis_data_block(tmp_path):
    f_hz, z = synth.randles_spectrum(noise=0.0)
    synth.write_pssession(tmp_path / "eis.pssession", [{"title": "Rct sensor", "method_id": "eis", "eis": (f_hz, z)}])
    c = load_pssession(tmp_path / "eis.pssession").curves[0]
    assert c.kind == "EIS" and set(c.extra) == {"freq", "zre", "zim"}
    assert np.allclose(c.extra["freq"], f_hz) and np.allclose(c.extra["zre"], z.real) and np.allclose(c.extra["zim"], z.imag)
    assert np.allclose(c.y, -z.imag) and (c.extra["zim"] < 0).all()                  # Nyquist y = -Z'', Im(Z) negative


def test_unrecognised_eis_layout_falls_back_instead_of_failing(tmp_path):
    synth.write_pssession(tmp_path / "x.pssession", [{"title": "weird", "method_id": "eis", "curves": [
        ("Z' vs -Z''", np.linspace(100, 1000, 20), np.linspace(1, 300, 20), "PalmSens.Units.Ohm", "PalmSens.Units.Ohm")]}])
    c = load_pssession(tmp_path / "x.pssession").curves[0]
    assert c.kind == "EIS" and not c.extra                                              # loaded, but flagged: no frequency


# ===================================================================== generic table import

def write(path, text, enc="utf-8"):
    path.write_bytes(text.encode(enc))
    return path


@pytest.mark.parametrize("sep", [",", ";", "\t"])
def test_delimiters_headers_and_units(tmp_path, sep):
    rows = [f"E (V){sep}i (µA)"] + [f"{0.1 * k:.3f}{sep}{2.0 * k:.3f}" for k in range(8)]
    t = importers.read_table(write(tmp_path / "a.txt", "\n".join(rows)))
    assert t.columns == ["E", "i"] and t.units == ["V", "µA"] and t.n_rows == 8
    assert t.data[3, 0] == pytest.approx(0.3) and t.data[7, 1] == pytest.approx(14.0)


def test_comments_before_the_header_and_slash_units(tmp_path):
    text = "# Gamry export\n# date 2026-11-03\nt/s\tE/mV\n" + "\n".join(f"{k}\t{100 + k}" for k in range(10))
    t = importers.read_table(write(tmp_path / "b.txt", text))
    assert t.columns == ["t", "E"] and t.units == ["s", "mV"] and t.n_rows == 10


def test_decimal_commas_and_thousands(tmp_path):
    text = "E (V);i (µA)\n" + "\n".join(f"{0.1 * k:.3f}".replace(".", ",") + ";" + f"{1234.5 * k:.1f}".replace(".", ",") for k in range(1, 7))
    t = importers.read_table(write(tmp_path / "c.csv", text))
    assert t.data[0, 0] == pytest.approx(0.1) and t.data[1, 1] == pytest.approx(2469.0) and "decimal comma" in t.notes[0]


def test_no_header_and_utf16_and_xlsx(tmp_path):
    t = importers.read_table(write(tmp_path / "d.txt", "\n".join(f"{k},{k * k}" for k in range(6))))
    assert t.header_row is None and t.columns == ["col1", "col2"] and t.data[5, 1] == 25
    u = importers.read_table(write(tmp_path / "e.csv", "E (V),i (µA)\n" + "\n".join(f"{k},{k}" for k in range(6)), "utf-16"))
    assert u.units == ["V", "µA"]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Frequency (Hz)", "Z' (Ohm)", "-Z'' (Ohm)"])
    for k in range(6):
        ws.append([1000.0 / (k + 1), 100 + k, 5 * k])
    wb.save(tmp_path / "f.xlsx")
    x = importers.read_table(tmp_path / "f.xlsx")
    assert x.columns[0] == "Frequency" and x.units[0] == "Hz" and x.data.shape == (6, 3)


def test_garbage_and_empty_files_are_refused_clearly(tmp_path):
    with pytest.raises(ValueError, match="no numeric rows"):
        importers.read_table(write(tmp_path / "g.txt", "just words here\nand more words\nno numbers at all"))
    with pytest.raises(ValueError, match="empty"):
        importers.read_table(write(tmp_path / "h.txt", "   \n\n"))


def test_voltammogram_import_converts_units_and_picks_columns(tmp_path):
    text = "Potential (mV)\tCurrent (nA)\n" + "\n".join(f"{100 * k}\t{5000 * k}" for k in range(1, 9))
    t = importers.read_table(write(tmp_path / "v.txt", text))
    roles = importers.guess_roles(t, "DPV")
    assert roles == {"x": 0, "y": 1}
    c = importers.build_curve(t, "DPV", roles, "DPV imported", uid="u1")
    assert c.kind == "DPV" and c.x_unit == "V" and c.y_unit == "µA"
    assert c.x[0] == pytest.approx(0.1) and c.y[-1] == pytest.approx(40.0)              # mV -> V, nA -> µA


def test_eis_import_sorts_by_frequency_and_fixes_the_sign(tmp_path):
    f_hz, z = synth.randles_spectrum()
    rows = ["Freq (kHz)\tZ' (kOhm)\t-Z'' (kOhm)"] + [f"{a / 1e3}\t{b.real / 1e3}\t{-b.imag / 1e3}" for a, b in zip(f_hz[::-1], z[::-1])]
    t = importers.read_table(write(tmp_path / "z.txt", "\n".join(rows)))
    roles = importers.guess_roles(t, "EIS")
    assert roles == {"freq": 0, "zre": 1, "zim": 2}
    c = importers.build_curve(t, "EIS", roles, "imported EIS")
    assert c.kind == "EIS" and np.allclose(c.extra["freq"], f_hz) and (np.diff(c.extra["freq"]) < 0).all()   # high -> low
    assert np.allclose(c.extra["zre"], z.real) and np.allclose(c.extra["zim"], z.imag) and (c.extra["zim"] < 0).all()
    assert np.allclose(c.y, -z.imag)
    with pytest.raises(ValueError):
        importers.build_curve(t, "EIS", {"freq": 0, "zre": 1, "zim": None}, "x")


def test_import_refuses_too_few_points(tmp_path):
    t = importers.read_table(write(tmp_path / "s.txt", "t (s)\tE (V)\n1\t2\n2\t3\n3\t4"))
    t.data[1:, 1] = np.nan
    with pytest.raises(ValueError):
        importers.build_curve(t, "OCP", {"x": 0, "y": 1}, "x")


def test_split_header():
    assert importers.split_header("E (V)") == ("E", "V") and importers.split_header("i/µA") == ("i", "µA")
    assert importers.split_header("Z' [Ohm]") == ("Z'", "Ohm") and importers.split_header("time") == ("time", "")


# ===================================================================== projects keep the new data

def test_project_round_trips_extra_columns_responses_and_extras(tmp_path):
    f_hz, z = synth.randles_spectrum()
    t = importers.read_table(write(tmp_path / "z.txt", "f (Hz)\tZ' (Ohm)\t-Z'' (Ohm)\n" + "\n".join(
        f"{a}\t{b.real}\t{-b.imag}" for a, b in zip(f_hz, z))))
    curve = importers.build_curve(t, "EIS", importers.guess_roles(t, "EIS"), "my EIS", uid="x#0")
    df = DataFile(path=tmp_path / "z.txt", fmt="table", curves=[curve])
    resp = [Response(id="r1", source_uid="x#0", technique="EIS", quantity="Rct", unit="Ω", value=1000.0, conc=10.0,
                     conc_unit="µM", condition="analyte", group="E1", time=2.5, role="standard", note="n")]
    pd_ = ProjectData(meta=ProjectMeta(name="t"), files=[df], responses=resp,
                      extras={"analysis_params": {"eis_fit|x#0": {"circuit": "randles"}}, "study": {"model": "linear"}})
    save_project(tmp_path / "p.echemproj", pd_, include_plots=False)
    back = load_project(tmp_path / "p.echemproj")
    c = back.files[0].curves[0]
    assert c.kind == "EIS" and set(c.extra) == {"freq", "zre", "zim"}
    assert all(np.array_equal(c.extra[k], curve.extra[k]) for k in c.extra)
    assert back.responses == resp
    assert back.extras == pd_.extras
