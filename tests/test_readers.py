"""The reader registry: which reader claims a file, and what the rest of the program sees."""
import numpy as np
import pytest

import synth
from echem_analyzer import io_palmsens, readers
from echem_analyzer.readers import Reader, UnsupportedFile


def _palmsens_csv(path):
    t = np.linspace(0, 10, 101)
    synth.write_pstrace_csv(path, [("CA: i vs t", t, 1 + 0 * t, "s", "µA")])
    return path


# ===================================================================== registry

def test_palmsens_files_are_claimed_by_the_palmsens_readers(tmp_path):
    csv = _palmsens_csv(tmp_path / "run.csv")
    t = np.linspace(0, 10, 11)
    synth.write_pssession(tmp_path / "s.pssession", [
        {"title": "step", "method_id": "ad", "curves": [("i vs t", t, 1 + 0 * t, "PalmSens.Units.Second", "PalmSens.Units.MicroAmpere")]}])
    assert readers.pick(csv).key == "csv"
    assert readers.pick(tmp_path / "s.pssession").key == "pssession"
    assert readers.load(csv).fmt == "csv" and readers.load(tmp_path / "s.pssession").fmt == "pssession"


def test_a_file_no_reader_recognises_is_unsupported_not_misread(tmp_path):
    other = tmp_path / "results.csv"
    other.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    assert readers.pick(other) is None
    with pytest.raises(UnsupportedFile):
        readers.load(other)
    assert issubclass(UnsupportedFile, ValueError)                    # older callers that catch ValueError still work
    assert readers.pick(tmp_path / "notes.docx") is None              # a suffix nobody handles


def test_a_folder_scan_lists_only_files_a_reader_recognises(tmp_path):
    _palmsens_csv(tmp_path / "b run.csv")
    _palmsens_csv(tmp_path / "a run.csv")
    (tmp_path / "results.csv").write_text("a,b\n1,2\n", encoding="utf-8")        # not an instrument file
    (tmp_path / "~$lock.csv").write_bytes(b"x")
    (tmp_path / ".hidden.csv").write_bytes(b"x")
    names = [p.name for p in readers.find_files(tmp_path)]
    assert names == ["a run.csv", "b run.csv"]
    assert [p.name for p in io_palmsens.find_data_files(tmp_path)] == names


def test_the_old_entry_points_follow_the_registry(tmp_path):
    csv = _palmsens_csv(tmp_path / "run.csv")
    assert io_palmsens.load_any(csv).curves
    assert io_palmsens.SUPPORTED_SUFFIXES == readers.suffixes()
    with pytest.raises(AttributeError):
        io_palmsens.NO_SUCH_THING                                       # noqa: B018 - the module hook only knows one name


def test_the_open_dialog_filter_lists_every_suffix():
    flt = readers.file_filter()
    for suffix in readers.suffixes():
        assert f"*{suffix}" in flt
    assert flt.endswith("All files (*)")


def test_a_reader_cannot_be_registered_twice():
    with pytest.raises(ValueError):
        readers.register(Reader("csv", "again", (".csv",), lambda head: True, lambda p: None))


def test_head_reading_survives_a_multibyte_character_cut_in_two(tmp_path):
    p = tmp_path / "u16.csv"
    p.write_bytes(("Date and time:,2026\r\n" + "µ" * 20000).encode("utf-16"))
    assert readers.pick(p).key == "csv"                               # the read stops in the middle of a UTF-16 pair


# ===================================================================== Gamry .DTA

def _gamry_cv_rows(cycle: int, rate: float = 0.1):
    e, i_ua = synth.cv_scan(1.0, 3.0)
    t = (cycle - 1) * e.size * 0.005 / rate + np.arange(e.size) * 0.005 / rate
    return [(k, float(t[k]), float(e[k]), float(i_ua[k] * 1e-6), float(e[k]), 0.0, 0.0, 0.0, 5, 0) for k in range(e.size)], e, i_ua


GAMRY_CV_COLS = ["Pt", "T", "Vf", "Im", "Vu", "Pwr", "Sig", "Ach", "IERange", "Over"]
GAMRY_CV_UNITS = ["#", "s", "V vs. Ref.", "A", "V", "W", "V", "V", "#", "bits"]


def test_gamry_cv_has_one_curve_per_cycle_in_the_app_s_units(tmp_path):
    (rows1, e, i_ua), (rows2, _, _) = _gamry_cv_rows(1)[:3], _gamry_cv_rows(2)[:3]
    synth.write_gamry_dta(tmp_path / "cv.DTA", "CV", "Cyclic Voltammetry",
                          {"VINIT": (0.0, "Initial E (V)"), "VLIM1": (0.6, "Scan Limit 1 (V)"), "VLIM2": (-0.2, "Scan Limit 2 (V)"),
                           "SCANRATE": (100.0, "Scan Rate (mV/s)"), "CYCLES": (2, "Cycles")},
                          [("CURVE1", GAMRY_CV_COLS, GAMRY_CV_UNITS, rows1), ("CURVE2", GAMRY_CV_COLS, GAMRY_CV_UNITS, rows2)])
    assert readers.pick(tmp_path / "cv.DTA").key == "gamry"                        # the suffix is matched in any case
    f = readers.load(tmp_path / "cv.DTA")
    assert f.fmt == "gamry" and [c.scan for c in f.curves] == [1, 2] and {c.kind for c in f.curves} == {"CV"}
    c = f.curves[0]
    assert np.allclose(c.x, e) and np.allclose(c.y, i_ua, atol=1e-9) and (c.x_unit, c.y_unit) == ("V", "µA")
    assert c.meta["scan_rate"] == pytest.approx(0.1)                               # stated in mV/s, kept in V/s
    assert (c.meta["e_begin"], c.meta["e_vtx1"], c.meta["e_vtx2"]) == (0.0, 0.6, -0.2)
    assert c.meta["run_title"] == "Cyclic Voltammetry" and c.meta["device"] == "REF600-12345"
    assert [x.uid for x in f.curves] == ["cv.DTA#0", "cv.DTA#1"]


def test_gamry_cv_with_all_cycles_in_one_table_is_cut_at_the_vertices(tmp_path):
    rows1, e, _ = _gamry_cv_rows(1)[:3]
    rows2 = _gamry_cv_rows(2)[0]
    rows = [(k, *r[1:]) for k, r in enumerate(rows1 + rows2)]
    synth.write_gamry_dta(tmp_path / "cv.dta", "CV", "CV", {"CYCLES": (2, "Cycles")}, [("CURVE", GAMRY_CV_COLS, GAMRY_CV_UNITS, rows)])
    f = readers.load(tmp_path / "cv.dta")
    assert [c.scan for c in f.curves] == [1, 2]
    assert f.curves[0].meta["scan_rate"] == pytest.approx(0.1, rel=0.02)           # not stated: worked out from time and potential
    assert "estimated" in f.curves[0].meta["scan_rate_source"]


def test_gamry_chronoamperometry_is_a_ca_curve_with_its_step_potential(tmp_path):
    t = np.linspace(0, 20, 201)
    i = 2e-6 + 1e-7 * (t > 10)
    rows = [(k, float(t[k]), 0.3, float(i[k]), 0.3, 0.0, 0.0, 0.0, 5, 0) for k in range(t.size)]
    synth.write_gamry_dta(tmp_path / "ca.dta", "CHRONOA", "Chronoamperometry", {"VSTEP1": (0.3, "Step 1 Voltage (V)")},
                          [("CURVE", GAMRY_CV_COLS, GAMRY_CV_UNITS, rows)])
    c = readers.load(tmp_path / "ca.dta").curves[0]
    assert c.kind == "CA" and np.allclose(c.x, t) and np.allclose(c.y, i * 1e6) and c.x_unit == "s"
    assert c.meta["E"] == 0.3 and c.meta["dt"] == pytest.approx(0.1)


def test_gamry_open_circuit_potential_keeps_volts(tmp_path):
    t = np.linspace(0, 60, 61)
    v = 0.25 - 1e-3 * np.exp(-t / 10)
    rows = [(k, float(t[k]), float(v[k]), 0.0, 5, 0) for k in range(t.size)]
    synth.write_gamry_dta(tmp_path / "ocp.dta", "CORPOT", "Open Circuit Potential",
                          tables=[("OCVCURVE", ["Pt", "T", "Vm", "Vsig", "Ach", "Over"], ["#", "s", "V vs. Ref.", "V", "V", "bits"], rows)])
    c = readers.load(tmp_path / "ocp.dta").curves[0]
    assert c.kind == "OCP" and np.allclose(c.y, v) and (c.x_unit, c.y_unit) == ("s", "V")


def test_gamry_impedance_follows_the_app_s_sign_convention(tmp_path):
    f_hz, z = synth.randles_spectrum()
    rows = [(k, float(k), float(f_hz[k]), float(z[k].real), float(z[k].imag), 0.0, 0.0, 0.0, 0.0, 0.0, 5) for k in range(f_hz.size)]
    cols = ["Pt", "Time", "Freq", "Zreal", "Zimag", "Zsig", "Zmod", "Zphz", "Idc", "Vdc", "IERange"]
    synth.write_gamry_dta(tmp_path / "eis.dta", "EISPOT", "Potentiostatic EIS",
                          tables=[("ZCURVE", cols, ["#", "s", "Hz", "ohm", "ohm", "V", "ohm", "°", "A", "V", "#"], rows)])
    c = readers.load(tmp_path / "eis.dta").curves[0]
    assert c.kind == "EIS" and set(c.extra) == {"freq", "zre", "zim"}
    assert np.allclose(c.extra["freq"], f_hz) and np.allclose(c.extra["zre"], z.real, rtol=1e-5)
    assert (c.extra["zim"] < 0).all() and np.allclose(c.y, -c.extra["zim"])


def test_gamry_differential_pulse_uses_the_differential_current(tmp_path):
    e, i = synth.dpv_trace(5.0)
    rows = [(k, float(k), float(e[k]), float(e[k]), float(i[k] * 1e-6), float(i[k] * 1e-6 * 0.5)) for k in range(e.size)]
    synth.write_gamry_dta(tmp_path / "dpv.dta", "DPV", "Differential Pulse",
                          tables=[("CURVE", ["Pt", "T", "Vstep", "Vf", "Idif", "Im"], ["#", "s", "V", "V", "A", "A"], rows)])
    c = readers.load(tmp_path / "dpv.dta").curves[0]
    assert c.kind == "DPV" and np.allclose(c.y, i, atol=1e-6) and np.allclose(c.x, e)


def test_gamry_decimal_commas_and_unknown_tables(tmp_path):
    t = np.linspace(0, 5, 11)
    rows = [(k, float(t[k]), 0.3, float(t[k]) * 1e-6, 0.3, 0.0, 0.0, 0.0, 5, 0) for k in range(t.size)]
    synth.write_gamry_dta(tmp_path / "ca.dta", "CHRONOA", "Amperometry", tables=[("CURVE", GAMRY_CV_COLS, GAMRY_CV_UNITS, rows)], decimal=",")
    assert np.allclose(readers.load(tmp_path / "ca.dta").curves[0].y, t)
    synth.write_gamry_dta(tmp_path / "empty.dta", "CV", "CV")
    with pytest.raises(ValueError, match="no curves"):
        readers.load(tmp_path / "empty.dta")


def test_a_dta_that_is_not_a_gamry_file_is_not_claimed(tmp_path):
    (tmp_path / "x.dta").write_text("just some text\n1 2 3\n", encoding="utf-8")
    assert readers.pick(tmp_path / "x.dta") is None


# ===================================================================== CH Instruments .txt

def test_chi_cv_is_cut_into_scans_at_the_reversal_points(tmp_path):
    e1, i1 = synth.cv_scan(1.0, 3.0)
    e, i_ua = np.r_[e1, e1], np.r_[i1, i1]
    synth.write_chi_txt(tmp_path / "cv.txt", "Cyclic Voltammetry", ["Potential/V", "Current/A"],
                        list(zip(e, i_ua * 1e-6)),
                        {"Init E (V)": -0.2, "High E (V)": 0.6, "Low E (V)": -0.2, "Init P/N": "P",
                         "Scan Rate (V/s)": 0.1, "Segment": 4, "Sample Interval (V)": 0.005})
    assert readers.pick(tmp_path / "cv.txt").key == "chi"
    f = readers.load(tmp_path / "cv.txt")
    assert f.fmt == "chi" and [c.scan for c in f.curves] == [1, 2] and {c.kind for c in f.curves} == {"CV"}
    c = f.curves[0]
    assert abs(c.n - e1.size) <= 1 and np.allclose(c.x[:e1.size], e1) and np.allclose(c.y[:e1.size], i1, rtol=1e-5, atol=1e-6)
    assert (c.x_unit, c.y_unit) == ("V", "µA") and c.meta["scan_rate"] == 0.1
    assert (c.meta["e_begin"], c.meta["e_vtx1"], c.meta["e_vtx2"], c.meta["e_step"]) == (-0.2, 0.6, -0.2, 0.005)
    assert c.meta["run_title"] == "Cyclic Voltammetry" and c.meta["device"] == "CHI660E"


def test_chi_a_single_cv_sweep_pair_is_scan_one_and_a_negative_start_swaps_the_vertices(tmp_path):
    e, i = synth.cv_scan(1.0, 3.0)
    synth.write_chi_txt(tmp_path / "cv.txt", "Cyclic Voltammetry", ["Potential/V", "Current/A"], list(zip(e, i * 1e-6)),
                        {"High E (V)": 0.6, "Low E (V)": -0.2, "Init P/N": "N", "Scan Rate (mV/s)": 100})
    f = readers.load(tmp_path / "cv.txt")
    assert [c.scan for c in f.curves] == [1]
    assert (f.curves[0].meta["e_vtx1"], f.curves[0].meta["e_vtx2"]) == (-0.2, 0.6) and f.curves[0].meta["scan_rate"] == pytest.approx(0.1)


def test_chi_amperometry_potentiometry_and_pulse_techniques(tmp_path):
    t = np.linspace(0, 30, 61)
    i = 1.5e-6 + 2e-7 * (t > 15)
    synth.write_chi_txt(tmp_path / "ca.txt", "Amperometric i-t Curve", ["Time/sec", "Current/A"], list(zip(t, i)), {"Init E (V)": 0.4})
    ca = readers.load(tmp_path / "ca.txt").curves[0]
    assert ca.kind == "CA" and np.allclose(ca.y, i * 1e6) and ca.meta["E"] == 0.4 and ca.meta["dt"] == pytest.approx(0.5)

    v = 0.12 + 1e-3 * np.exp(-t / 5)
    synth.write_chi_txt(tmp_path / "ocp.txt", "Open Circuit Potential - Time", ["Time/sec", "Potential/V"], list(zip(t, v)))
    ocp = readers.load(tmp_path / "ocp.txt").curves[0]
    assert ocp.kind == "OCP" and np.allclose(ocp.y, v) and ocp.y_unit == "V"

    e, ip = synth.dpv_trace(5.0)
    synth.write_chi_txt(tmp_path / "dpv.txt", "Differential Pulse Voltammetry", ["Potential/V", "Current/A"], list(zip(e, ip * 1e-6)))
    dpv = readers.load(tmp_path / "dpv.txt").curves[0]
    assert dpv.kind == "DPV" and np.allclose(dpv.x, e) and np.allclose(dpv.y, ip, rtol=1e-5, atol=1e-6)


@pytest.mark.parametrize("sign", [1, -1])
def test_chi_impedance_is_oriented_whatever_the_sign_of_z_imag(tmp_path, sign):
    f_hz, z = synth.randles_spectrum()
    synth.write_chi_txt(tmp_path / "eis.txt", "A.C. Impedance", ["Freq/Hz", "Z'/ohm", "Z\"/ohm", "Z/ohm", "Phase/deg"],
                        [(f, zz.real, sign * -zz.imag, abs(zz), 0.0) for f, zz in zip(f_hz, z)])
    c = readers.load(tmp_path / "eis.txt").curves[0]
    assert c.kind == "EIS" and np.allclose(c.extra["freq"], f_hz) and (c.extra["zim"] < 0).all()
    assert np.allclose(c.extra["zre"], z.real, rtol=1e-5)


def test_chi_a_text_table_without_the_instrument_lines_is_not_claimed(tmp_path):
    (tmp_path / "plain.txt").write_text("Potential/V, Current/A\n\n0.1, 1e-6\n0.2, 2e-6\n", encoding="utf-8")
    assert readers.pick(tmp_path / "plain.txt") is None                       # a plain table belongs to the Import table
