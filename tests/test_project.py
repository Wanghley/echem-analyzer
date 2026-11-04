import csv
import io
import json
import os
import zipfile
from pathlib import Path

import numpy as np
import pytest

from echem_analyzer import project as proj
from echem_analyzer.ca_analysis import analyze_ca, detect_steps
from echem_analyzer.cv_analysis import analyze_run, group_cv_runs
from echem_analyzer.io_palmsens import load_csv, load_pssession
from echem_analyzer.models import KIND_CA, CAAnalysisSettings, CVSettings
from echem_analyzer.project import (
    FORMAT,
    VERSION,
    ProjectData,
    ProjectError,
    ProjectMeta,
    load_project,
    save_project,
)


@pytest.fixture()
def data(data_dir) -> ProjectData:
    b2 = load_csv(data_dir / "2026-11-02 B2 GY data.csv")
    a6 = load_pssession(data_dir / "2026-11-02 A6 GY data.pssession")
    for f in (b2, a6):
        for k, c in enumerate(f.curves):
            c.uid = f"{f.path}#{k}"
    ca = b2.of_kind(KIND_CA)[0]
    s = CAAnalysisSettings(step_times=[float(t) for t in detect_steps(ca.x[:-1], ca.y[:-1])],
                           stock_conc=505.1, v0_ml=10.0, v_add_ul=10.0, method="drift")
    s.step_volumes_ul = [10.0, 20.0, 10.0, 10.0, 10.0]
    s.step_included = [True, True, False, True, True]
    run = group_cv_runs(b2.curves)[0]
    return ProjectData(
        meta=ProjectMeta(name="B2 conditioning", operator="W", electrode="B2", analyte="GY", matrix="CSF",
                         notes="line one\nline two — ünïcode µ"),
        files=[b2, a6],
        ca_settings={ca.uid: s},
        cv_settings={run.uid: CVSettings(scan_rate=0.05, baseline="linear", anodic_window=[0.0, 0.3],
                                         excluded_cycles=[1])},
        ui={"tab": 1, "ca_uid": ca.uid, "checked": [ca.uid]},
        app_version="0.2.0",
    )


# ------------------------------------------------------------------ round trip

def test_roundtrip_is_exact(tmp_path, data):
    p = tmp_path / "p.echemproj"
    save_project(p, data)
    back = load_project(p)

    assert [f.path for f in back.files] == [f.path for f in data.files]
    assert [f.fmt for f in back.files] == [f.fmt for f in data.files]
    for fa, fb in zip(data.files, back.files):
        assert [c.uid for c in fa.curves] == [c.uid for c in fb.curves]
        for ca, cb in zip(fa.curves, fb.curves):
            assert np.array_equal(ca.x, cb.x) and np.array_equal(ca.y, cb.y)       # bit-exact data
            assert (ca.kind, ca.title, ca.x_unit, ca.y_unit, ca.scan) == (cb.kind, cb.title, cb.x_unit, cb.y_unit, cb.scan)
            assert ca.meta == cb.meta and ca.source == cb.source
    assert {u: s.to_dict() for u, s in back.ca_settings.items()} == {u: s.to_dict() for u, s in data.ca_settings.items()}
    assert {u: s.to_dict() for u, s in back.cv_settings.items()} == {u: s.to_dict() for u, s in data.cv_settings.items()}
    assert back.ui == data.ui and back.app_version == "0.2.0"
    assert back.meta.name == "B2 conditioning" and back.meta.notes == data.meta.notes
    assert back.meta.created and back.meta.modified


def test_project_is_self_contained(tmp_path, data_dir):
    """After saving, the original data files can vanish and the project still opens, intact."""
    src = tmp_path / "raw"
    src.mkdir()
    csv_copy = src / "run.csv"
    csv_copy.write_bytes((data_dir / "2026-11-02 B2 GY data.csv").read_bytes())
    f = load_csv(csv_copy)
    for k, c in enumerate(f.curves):
        c.uid = f"{csv_copy}#{k}"
    original = [(c.uid, c.x.copy(), c.y.copy()) for c in f.curves]
    p = tmp_path / "p.echemproj"
    save_project(p, ProjectData(files=[f]))

    csv_copy.unlink()                                  # the raw file is gone
    src.rmdir()
    assert not csv_copy.exists()
    back = load_project(p)
    got = [(c.uid, c.x, c.y) for c in back.files[0].curves]
    assert [g[0] for g in got] == [o[0] for o in original]
    assert all(np.array_equal(g[1], o[1]) and np.array_equal(g[2], o[2]) for g, o in zip(got, original))


def test_analysis_after_roundtrip_matches_the_original(tmp_path, data):
    p = tmp_path / "p.echemproj"
    save_project(p, data)
    back = load_project(p)
    ca = data.files[0].of_kind(KIND_CA)[0]
    cb = back.files[0].of_kind(KIND_CA)[0]
    a = analyze_ca(ca.x, ca.y, data.ca_settings[ca.uid])
    b = analyze_ca(cb.x, cb.y, back.ca_settings[cb.uid])
    assert a.fit.slope == b.fit.slope and a.c_x == b.c_x and a.lod == b.lod


def test_timestamps_created_once_modified_each_save(tmp_path, data):
    p = tmp_path / "p.echemproj"
    m1 = save_project(p, data)
    data.meta = m1
    m2 = save_project(p, data)
    assert m1.created == m2.created and m1.created
    assert m2.modified >= m1.modified


# ------------------------------------------------------------------ results snapshot

def test_results_snapshot_is_in_the_archive_and_correct(tmp_path, data):
    p = tmp_path / "p.echemproj"
    save_project(p, data)
    with zipfile.ZipFile(p) as z:
        names = set(z.namelist())
        assert {"project.json", "data/curves.npz", "results/ca_summary.csv", "results/cv_summary.csv"} <= names
        assert any(n.startswith("results/ca/") and n.endswith("__steps.csv") for n in names)
        assert any(n.startswith("results/cv/") and n.endswith("__cycles.csv") for n in names)
        rows = list(csv.DictReader(io.StringIO(z.read("results/ca_summary.csv").decode())))
        cyc = list(csv.DictReader(io.StringIO(next(z.read(n).decode() for n in names if n.endswith("__cycles.csv")))))
    assert len(rows) == 1
    row = rows[0]
    assert (row["project"], row["electrode"], row["analyte"], row["matrix"]) == ("B2 conditioning", "B2", "GY", "CSF")
    ca = data.files[0].of_kind(KIND_CA)[0]
    expect = analyze_ca(ca.x, ca.y, data.ca_settings[ca.uid])
    assert float(row["slope"]) == pytest.approx(expect.fit.slope) and float(row["LOD"]) == pytest.approx(expect.lod)
    assert row["method"] == "drift" and int(row["n_additions"]) == 5
    # CV: cycle 1 excluded -> still listed, flagged
    assert [c["cycle"] for c in cyc] == ["1", "2", "3", "4", "5"]
    assert cyc[0]["included"] == "False" and cyc[1]["included"] == "True"
    # the snapshot must equal a direct analysis with the same (baseline-corrected, windowed) settings
    run = group_cv_runs(c for f in data.files for c in f.curves)[0]
    expect = {r.cycle: r for r in analyze_run(run, next(iter(data.cv_settings.values())), include_excluded=True)}
    for row in cyc:
        r = expect[int(row["cycle"])]
        assert float(row["i_pa_uA"]) == pytest.approx(r.i_pa, rel=1e-9)
        assert float(row["E_pa_V"]) == pytest.approx(r.e_pa, rel=1e-9)
    assert float(cyc[1]["i_pa_uA"]) != pytest.approx(10.333, abs=0.01)     # i.e. it IS baseline-corrected


def test_runs_never_viewed_have_no_results(tmp_path, data):
    data.ca_settings.clear()
    data.cv_settings.clear()
    p = tmp_path / "p.echemproj"
    save_project(p, data)
    with zipfile.ZipFile(p) as z:
        assert not [n for n in z.namelist() if n.startswith("results/")]


# ------------------------------------------------------------------ crash safety

def test_failed_save_leaves_the_existing_project_intact(tmp_path, data, monkeypatch):
    p = tmp_path / "p.echemproj"
    save_project(p, data)
    good = p.read_bytes()

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        save_project(p, data)
    assert p.read_bytes() == good                                   # untouched
    assert not list(tmp_path.glob("*.tmp"))                         # temp file cleaned up
    monkeypatch.undo()
    assert load_project(p).meta.name == "B2 conditioning"


def test_save_creates_missing_folders(tmp_path, data):
    p = tmp_path / "a" / "b" / "p.echemproj"
    save_project(p, data)
    assert p.is_file()


def test_empty_project_roundtrips(tmp_path):
    p = tmp_path / "empty.echemproj"
    save_project(p, ProjectData())
    back = load_project(p)
    assert back.files == [] and back.ca_settings == {} and back.cv_settings == {}


# ------------------------------------------------------------------ damaged / hostile files

def _zip_with(path, members: dict[str, bytes]):
    with zipfile.ZipFile(path, "w") as z:
        for n, b in members.items():
            z.writestr(n, b)


def test_not_a_zip(tmp_path):
    p = tmp_path / "x.echemproj"
    p.write_text("hello")
    with pytest.raises(ProjectError, match="not a readable project"):
        load_project(p)


def test_zip_without_project_json(tmp_path):
    p = tmp_path / "x.echemproj"
    _zip_with(p, {"other.txt": b"hi"})
    with pytest.raises(ProjectError, match="missing 'project.json'"):
        load_project(p)


def test_wrong_format_marker(tmp_path):
    p = tmp_path / "x.echemproj"
    _zip_with(p, {"project.json": json.dumps({"format": "something-else", "version": 1}).encode()})
    with pytest.raises(ProjectError, match="not an Echem Analyzer project"):
        load_project(p)


def test_newer_version_is_refused_with_a_clear_message(tmp_path):
    p = tmp_path / "x.echemproj"
    _zip_with(p, {"project.json": json.dumps({"format": FORMAT, "version": VERSION + 1}).encode()})
    with pytest.raises(ProjectError, match="newer version"):
        load_project(p)


def test_damaged_json(tmp_path):
    p = tmp_path / "x.echemproj"
    _zip_with(p, {"project.json": b"{not json"})
    with pytest.raises(ProjectError, match="damaged"):
        load_project(p)


def test_missing_or_damaged_data_block(tmp_path, data):
    p = tmp_path / "x.echemproj"
    save_project(p, data)
    with zipfile.ZipFile(p) as z:
        doc = z.read("project.json")
    bad = tmp_path / "nodata.echemproj"
    _zip_with(bad, {"project.json": doc})
    with pytest.raises(ProjectError, match="data/curves.npz"):
        load_project(bad)
    junk = tmp_path / "junk.echemproj"
    _zip_with(junk, {"project.json": doc, "data/curves.npz": b"not an npz"})
    with pytest.raises(ProjectError):
        load_project(junk)


PICKLE_RAN: list[int] = []


def _mark():
    PICKLE_RAN.append(1)
    return 0.0


class _Evil:
    """Unpickling this calls ``_mark``: a stand-in for a malicious payload."""

    def __reduce__(self):
        return (_mark, ())


def test_pickled_payloads_are_never_executed(tmp_path, data):
    p = tmp_path / "x.echemproj"
    save_project(p, data)
    with zipfile.ZipFile(p) as z:
        doc = z.read("project.json")
    evil = io.BytesIO()
    np.savez(evil, **{f"{a}{k}": np.array([_Evil()], dtype=object) for k in range(60) for a in "xy"})

    # control: the payload really is live if pickle loading were allowed
    PICKLE_RAN.clear()
    np.load(io.BytesIO(evil.getvalue()), allow_pickle=True)["x0"]
    assert PICKLE_RAN, "test payload is inert, so this test would prove nothing"

    PICKLE_RAN.clear()
    bad = tmp_path / "evil.echemproj"
    _zip_with(bad, {"project.json": doc, "data/curves.npz": evil.getvalue()})
    with pytest.raises(ProjectError):
        load_project(bad)
    assert PICKLE_RAN == []                                   # never executed


def test_inconsistent_curve_lengths(tmp_path, data):
    p = tmp_path / "x.echemproj"
    save_project(p, data)
    with zipfile.ZipFile(p) as z:
        doc = z.read("project.json")
    arrs = io.BytesIO()
    np.savez(arrs, **{f"{a}{k}": np.zeros(3 if a == "x" else 4) for k in range(60) for a in "xy"})
    bad = tmp_path / "len.echemproj"
    _zip_with(bad, {"project.json": doc, "data/curves.npz": arrs.getvalue()})
    with pytest.raises(ProjectError, match="inconsistent"):
        load_project(bad)


def test_unknown_settings_keys_from_a_future_version_are_ignored(tmp_path, data):
    p = tmp_path / "x.echemproj"
    save_project(p, data)
    with zipfile.ZipFile(p) as z:
        members = {n: z.read(n) for n in z.namelist()}
    doc = json.loads(members["project.json"])
    for d in doc["ca_settings"].values():
        d["a_future_option"] = 123
    for d in doc["cv_settings"].values():
        d["another_future_option"] = "x"
    members["project.json"] = json.dumps(doc).encode()
    q = tmp_path / "future.echemproj"
    _zip_with(q, members)
    back = load_project(q)
    assert next(iter(back.ca_settings.values())).stock_conc == 505.1
    assert next(iter(back.cv_settings.values())).baseline == "linear"


def test_oversized_member_is_refused(tmp_path, data, monkeypatch):
    p = tmp_path / "x.echemproj"
    save_project(p, data)
    monkeypatch.setattr(proj, "MAX_MEMBER_BYTES", 10)
    with pytest.raises(ProjectError, match="implausibly large"):
        load_project(p)
