import numpy as np

from echem_analyzer.io_palmsens import (
    find_data_files,
    load_any,
    load_csv,
    load_pssession,
    parse_stock_from_title,
)
from echem_analyzer.models import KIND_CA, KIND_CV


def test_csv_b2(data_dir):
    f = load_csv(data_dir / "2026-11-02 B2 GY data.csv")
    ca = f.of_kind(KIND_CA)
    cv = f.of_kind(KIND_CV)
    assert len(ca) == 1 and len(cv) == 5
    assert ca[0].n == 1349
    assert abs(ca[0].meta["dt"] - 0.1) < 1e-4      # time axis is float32 in the export
    assert ca[0].x_unit == "s" and ca[0].y_unit == "µA"
    assert sorted(c.scan for c in cv) == [1, 2, 3, 4, 5]
    assert all(c.x_unit == "V" and c.y_unit == "µA" for c in cv)
    assert ca[0].y[0] < 0 and abs(ca[0].y[0] + 0.3256) < 1e-3
    assert f.exported_at.startswith("2026-11-02")


def test_csv_b5(data_dir):
    f = load_csv(data_dir / "2026-11-02 B5 CSF C1 data.csv")
    assert f.of_kind(KIND_CA)[0].n == 1765
    assert len(f.of_kind(KIND_CV)) == 5


def test_pssession_a6(data_dir):
    f = load_pssession(data_dir / "2026-11-02 A6 GY data.pssession")
    runs = {c.meta["run_title"] for c in f.curves}
    assert {"A6 CV", "calibration", "std addition GY 505.1mM"} <= runs
    ca = f.of_kind(KIND_CA)
    assert len(ca) == 2
    assert {c.n for c in ca} == {2294, 774}
    assert all(c.y_unit == "µA" for c in ca)
    assert abs(ca[0].meta["E"] - (-0.2)) < 1e-6
    assert len(f.of_kind(KIND_CV)) == 5


def test_pssession_b2_has_extra_run(data_dir):
    """B2's .pssession holds a CA run that was never exported to CSV."""
    f = load_pssession(data_dir / "2026-11-02 B2 GY data.pssession")
    assert sorted(c.n for c in f.of_kind(KIND_CA)) == [1349, 1726]


def test_csv_and_pssession_agree(data_dir):
    a = load_csv(data_dir / "2026-11-02 B2 GY data.csv").of_kind(KIND_CA)[0]
    b = next(
        c for c in load_pssession(data_dir / "2026-11-02 B2 GY data.pssession").of_kind(KIND_CA)
        if c.n == 1349
    )
    assert np.allclose(a.x, b.x, atol=1e-6)
    assert np.allclose(a.y, b.y, atol=1e-6)


def test_parse_stock():
    assert parse_stock_from_title("std addition GY 505.1mM") == (505.1, "mM")
    assert parse_stock_from_title("50 uM stock") == (50.0, "µM")
    assert parse_stock_from_title("calibration") is None


def test_find_and_load_any(data_dir):
    files = find_data_files(data_dir)
    names = {p.name for p in files}
    assert "2026-11-02 B2 GY data.csv" in names
    assert not any(n.startswith("~$") for n in names)
    for p in files:
        assert load_any(p).curves
