import json

import pytest

from echem_analyzer.models import CAAnalysisSettings, CVSettings
from echem_analyzer.presets import (
    KIND_CA,
    KIND_CV,
    PresetStore,
    apply_ca_preset,
    apply_cv_preset,
    ca_preset_from_settings,
    cv_preset_from_settings,
)


def run_settings():
    s = CAAnalysisSettings(
        stock_conc=505.1, stock_unit="mM", v0_ml=10.0, v_add_ul=10.0, method="drift", settle=4.0,
        step_times=[20.0, 41.7, 65.3], ignored_events=[36.0],
        step_conc_override=[None, 0.5, None], step_included=[True, False, True],
    )
    s.step_volumes_ul = [10.0, 10.0, 10.0]
    return s


# ------------------------------------------------------------------ store

def test_store_roundtrip_and_persistence(tmp_path):
    p = tmp_path / "cfg" / "presets.json"
    st = PresetStore(p)
    assert st.names(KIND_CA) == []
    st.save(KIND_CA, "GY 505 mM", {"stock_conc": 505.1})
    st.save(KIND_CA, "alpha", {"stock_conc": 1.0})
    st.save(KIND_CV, "scan 50 mV/s", {"scan_rate": 0.05})
    assert st.names(KIND_CA) == ["alpha", "GY 505 mM"]              # case-insensitive order
    assert json.loads(p.read_text(encoding="utf-8"))["version"] == 1   # valid JSON on disk
    again = PresetStore(p)                                           # a fresh process sees them
    assert again.get(KIND_CA, "GY 505 mM") == {"stock_conc": 505.1}
    assert again.get(KIND_CV, "scan 50 mV/s") == {"scan_rate": 0.05}
    assert again.get(KIND_CA, "missing") is None
    assert again.delete(KIND_CA, "alpha") and not again.delete(KIND_CA, "alpha")
    assert PresetStore(p).names(KIND_CA) == ["GY 505 mM"]
    assert not list(p.parent.glob("*.tmp"))                          # no temp files left behind


def test_overwriting_a_preset_replaces_it(tmp_path):
    st = PresetStore(tmp_path / "p.json")
    st.save(KIND_CA, "x", {"stock_conc": 1.0})
    st.save(KIND_CA, "x", {"stock_conc": 2.0})
    assert st.names(KIND_CA) == ["x"] and st.get(KIND_CA, "x") == {"stock_conc": 2.0}


def test_empty_name_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        PresetStore(tmp_path / "p.json").save(KIND_CA, "   ", {})


def test_corrupt_store_is_moved_aside_not_overwritten(tmp_path):
    p = tmp_path / "p.json"
    p.write_text("{ this is not json", encoding="utf-8")
    st = PresetStore(p)
    assert st.names(KIND_CA) == [] and "unreadable" in st.last_error
    assert (tmp_path / "p.json.corrupt").read_text(encoding="utf-8") == "{ this is not json"
    st.save(KIND_CA, "fresh", {"stock_conc": 3.0})                   # store is usable again
    assert PresetStore(p).get(KIND_CA, "fresh") == {"stock_conc": 3.0}


def test_wrong_shape_store_is_treated_as_corrupt(tmp_path):
    p = tmp_path / "p.json"
    p.write_text(json.dumps({"version": 1, "ca": ["not", "a", "dict"]}), encoding="utf-8")
    st = PresetStore(p)
    assert st.names(KIND_CA) == [] and st.last_error


# ------------------------------------------------------------------ CA presets

def test_ca_preset_holds_the_protocol_but_not_the_run_data():
    d = ca_preset_from_settings(run_settings())
    assert d["stock_conc"] == 505.1 and d["method"] == "drift" and d["settle"] == 4.0
    for per_run in ("step_times", "ignored_events", "step_conc_override", "step_included"):
        assert per_run not in d
    assert "step_volumes_ul" not in d                               # all equal to v_add -> no schedule


def test_ca_preset_keeps_a_real_volume_schedule():
    s = run_settings()
    s.step_volumes_ul = [10.0, 20.0, 50.0]
    assert ca_preset_from_settings(s)["step_volumes_ul"] == [10.0, 20.0, 50.0]


def test_applying_a_preset_never_touches_the_run_data():
    preset = ca_preset_from_settings(run_settings())
    target = CAAnalysisSettings(step_times=[5.0, 15.0], ignored_events=[9.0],
                                step_conc_override=[0.1, None], step_included=[False, True])
    ignored = apply_ca_preset(target, preset)
    assert ignored == []
    assert target.stock_conc == 505.1 and target.method == "drift" and target.v0_ml == 10.0
    assert target.step_times == [5.0, 15.0] and target.ignored_events == [9.0]
    assert target.step_conc_override == [0.1, None] and target.step_included == [False, True]


def test_volume_schedule_applies_by_index_and_pads():
    sched = {"v_add_ul": 10.0, "step_volumes_ul": [5.0, 25.0]}
    t = CAAnalysisSettings(step_times=[1.0, 2.0, 3.0])
    assert apply_ca_preset(t, sched) == []
    assert t.step_volumes_ul == [5.0, 25.0, 10.0]                    # third step gets the default volume


def test_invalid_values_are_ignored_and_reported():
    t = CAAnalysisSettings()
    bad = {"method": "drfit", "stock_conc": "lots", "stock_unit": "kg", "drop_last": True,
           "settle": 7.5, "unknown_field": 1}
    ignored = apply_ca_preset(t, bad)
    assert set(ignored) == {"method", "stock_conc", "stock_unit", "drop_last"}
    assert t.method == "plateau" and t.stock_conc == 0.0 and t.stock_unit == "mM" and t.drop_last == 1
    assert t.settle == 7.5                                            # valid fields still applied
    assert not hasattr(t, "unknown_field")


# ------------------------------------------------------------------ CV presets

def test_cv_preset_roundtrip_and_validation():
    s = CVSettings(scan_rate=0.05, anodic_window=[0.0, 0.3], baseline="linear", stability_tol=2.0,
                   excluded_cycles=[1])
    d = cv_preset_from_settings(s)
    assert "excluded_cycles" not in d                                # which cycles to drop is per run
    t = CVSettings(excluded_cycles=[3])
    assert apply_cv_preset(t, d) == []
    assert t.scan_rate == 0.05 and t.anodic_window == [0.0, 0.3] and t.baseline == "linear"
    assert t.stability_tol == 2.0 and t.excluded_cycles == [3]
    bad = {"baseline": "spline", "anodic_window": [1, 2, 3], "cathodic_window": "x", "scan_rate": -1}
    assert set(apply_cv_preset(CVSettings(), bad)) == {"baseline", "anodic_window", "cathodic_window", "scan_rate"}
    t2 = CVSettings()
    apply_cv_preset(t2, {"anodic_window": None, "cathodic_window": [0.3, -0.1]})
    assert t2.anodic_window is None and t2.cathodic_window == [0.3, -0.1]


def test_unknown_scan_rate_in_a_preset_keeps_the_runs_known_rate():
    run = CVSettings(scan_rate=0.05)                      # known from the .pssession
    apply_cv_preset(run, {"scan_rate": 0.0, "baseline": "linear"})
    assert run.scan_rate == 0.05 and run.baseline == "linear"
    assert "scan_rate" in apply_cv_preset(CVSettings(), {"scan_rate": -1.0})      # negative is invalid
    apply_cv_preset(run, {"scan_rate": 0.1})
    assert run.scan_rate == 0.1


def test_uniform_volumes_follow_the_presets_default_but_custom_ones_do_not():
    preset = {"v_add_ul": 10.0, "stock_conc": 505.1, "v0_ml": 10.0}
    fresh = CAAnalysisSettings(step_times=[1.0, 2.0, 3.0], v_add_ul=0.0)         # never customised
    apply_ca_preset(fresh, preset)
    assert fresh.step_volumes_ul == [10.0, 10.0, 10.0]

    custom = CAAnalysisSettings(step_times=[1.0, 2.0, 3.0], v_add_ul=5.0)
    custom.step_volumes_ul = [5.0, 20.0, 5.0]                                    # the user edited step 2
    apply_ca_preset(custom, preset)
    assert custom.v_add_ul == 10.0 and custom.step_volumes_ul == [5.0, 20.0, 5.0]

    scheduled = CAAnalysisSettings(step_times=[1.0, 2.0, 3.0], v_add_ul=5.0)
    apply_ca_preset(scheduled, {"v_add_ul": 10.0, "step_volumes_ul": [1.0, 2.0, 3.0]})
    assert scheduled.step_volumes_ul == [1.0, 2.0, 3.0]                          # an explicit schedule wins
