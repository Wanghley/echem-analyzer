"""The data package: what another person gets, and that it can be read without this program."""
import csv
import hashlib
import io
import json
import zipfile

import numpy as np

import synth
from echem_analyzer import acceptance as acc
from echem_analyzer import devices as dv
from echem_analyzer import package, readers
from echem_analyzer.models import ProjectData, ProjectMeta, Response


def make_project(tmp_path):
    t = np.linspace(0, 20, 201)
    synth.write_pstrace_csv(tmp_path / "run one.csv", [("CA: i vs t", t, 1.0 + 0.01 * t, "s", "µA")])
    f_hz, z = synth.randles_spectrum()
    synth.write_pssession(tmp_path / "eis.pssession", [{"title": "Rct sensor", "method_id": "eis", "eis": (f_hz, z)}])
    files = [readers.load(tmp_path / "run one.csv"), readers.load(tmp_path / "eis.pssession")]
    extras: dict = {}
    dv.save(extras, {"E1": dv.Device("E1", "thin", "L1", params={"ink (µL)": 2.0})})
    acc.save(extras, [acc.Rule("ΔEp", maximum=80.0)])
    rs = [Response(id="r1", source_label="E1 10 µM", quantity="Peak", unit="µA", value=8.123456789, conc=10.0, conc_unit="µM", group="E1",
                   role="standard", temp_c=25.0, note="café")]
    return ProjectData(meta=ProjectMeta(name="Ink study", analyte="dopamine", notes="Three days of work."), files=files, responses=rs, extras=extras), t


def read_zip(path):
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_the_package_holds_the_raw_files_the_curves_and_a_description(tmp_path):
    data, t = make_project(tmp_path)
    info = package.build_package(data, tmp_path / "pkg.zip")
    assert info["files"] == 2 and info["curves"] == 2 and info["raw_included"] == 2 and info["raw_missing"] == []
    names = read_zip(tmp_path / "pkg.zip")
    assert list(names)[:2] == ["README.md", "metadata.json"]                                      # the first things anyone opens
    assert {"responses.csv", "devices.csv", "acceptance.json", "settings.json"} <= set(names)
    raw = {n for n in names if n.startswith("raw/")}
    assert raw == {"raw/run_one.csv", "raw/eis.pssession"}
    assert names["raw/run_one.csv"] == (tmp_path / "run one.csv").read_bytes()                      # byte for byte
    meta = json.loads(names["metadata.json"])
    entry = next(f for f in meta["files"] if f["name"] == "run one.csv")
    assert entry["sha256"] == hashlib.sha256((tmp_path / "run one.csv").read_bytes()).hexdigest() and entry["raw"] == "included"
    assert meta["project"]["analyte"] == "dopamine" and meta["units"]["current"] == "µA" and meta["devices"] == 1 and meta["acceptance_rules"] == 1
    readme = names["README.md"].decode("utf-8")
    assert readme.startswith("# Ink study") and "dopamine" in readme and "2 of 2 found" in readme and "2 measurements" in readme


def test_a_curve_csv_has_units_in_its_header_and_the_exact_numbers(tmp_path):
    data, t = make_project(tmp_path)
    package.build_package(data, tmp_path / "pkg.zip")
    names = read_zip(tmp_path / "pkg.zip")
    member = next(n for n in names if n.startswith("curves/run_one"))
    rows = list(csv.reader(io.StringIO(names[member].decode("utf-8"))))
    assert rows[0] == ["x (s)", "y (µA)"] and len(rows) == 202
    assert np.array_equal(np.array([float(r[0]) for r in rows[1:]]), data.files[0].curves[0].x)         # repr: no digit lost
    assert np.array_equal(np.array([float(r[1]) for r in rows[1:]]), data.files[0].curves[0].y)
    eis = next(n for n in names if "eis__" in n)
    head = names[eis].decode("utf-8").splitlines()[0]
    assert head == "x (Ω),y (Ω),freq,zim,zre"                                                          # the spectrum keeps its other columns


def test_responses_devices_rules_and_settings_are_readable_data(tmp_path):
    data, _ = make_project(tmp_path)
    package.build_package(data, tmp_path / "pkg.zip")
    names = read_zip(tmp_path / "pkg.zip")
    rows = list(csv.DictReader(io.StringIO(names["responses.csv"].decode("utf-8"))))
    assert rows[0]["value"] == "8.123456789" and rows[0]["group"] == "E1" and rows[0]["temp_c"] == "25.0" and rows[0]["note"] == "café"
    assert rows[0]["sd"] == "" and rows[0]["use"] == "True"
    assert names["devices.csv"].decode("utf-8").splitlines()[1] == "E1,thin,L1,,,2"
    assert json.loads(names["acceptance.json"]) == [acc.Rule("ΔEp", maximum=80.0).to_dict()]
    settings = json.loads(names["settings.json"])
    assert set(settings) == {"ca", "cv", "extras"} and settings["extras"]["devices"][0]["id"] == "E1"


def test_a_raw_file_that_has_moved_is_reported_and_raw_can_be_left_out(tmp_path):
    data, _ = make_project(tmp_path)
    (tmp_path / "eis.pssession").unlink()
    info = package.build_package(data, tmp_path / "a.zip")
    assert info["raw_missing"] == ["eis.pssession"] and info["raw_included"] == 1 and info["curves"] == 2          # the curves are still there
    meta = json.loads(read_zip(tmp_path / "a.zip")["metadata.json"])
    assert {f["name"]: f["raw"] for f in meta["files"]} == {"run one.csv": "included", "eis.pssession": "missing"}
    lean = package.build_package(data, tmp_path / "b.zip", include_raw=False)
    assert lean["raw_included"] == 0 and not any(n.startswith("raw/") for n in read_zip(tmp_path / "b.zip"))
    assert json.loads(read_zip(tmp_path / "b.zip")["metadata.json"])["files"][0]["raw"] == "not stored"


def test_files_and_curves_with_the_same_name_do_not_overwrite_each_other(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    t = np.linspace(0, 5, 11)
    for d, level in (("a", 1.0), ("b", 2.0)):
        synth.write_pstrace_csv(tmp_path / d / "run.csv", [("CA: i vs t", t, level + 0 * t, "s", "µA")])
    data = ProjectData(files=[readers.load(tmp_path / "a" / "run.csv"), readers.load(tmp_path / "b" / "run.csv")])
    package.build_package(data, tmp_path / "p.zip")
    names = read_zip(tmp_path / "p.zip")
    assert sorted(n for n in names if n.startswith("raw/")) == ["raw/run.csv", "raw/run_2.csv"]
    assert names["raw/run.csv"] != names["raw/run_2.csv"] and len([n for n in names if n.startswith("curves/")]) == 2
    assert len({names[n] for n in names if n.startswith("curves/")}) == 2


def test_an_empty_project_still_gives_a_valid_package(tmp_path):
    info = package.build_package(ProjectData(), tmp_path / "e.zip")
    names = read_zip(tmp_path / "e.zip")
    assert info["curves"] == 0 and "responses.csv" in names and names["README.md"].decode("utf-8").startswith("# Echem Analyzer data package")
    assert "devices.csv" not in names and "acceptance.json" not in names
