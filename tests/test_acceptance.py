"""Electrode acceptance rules and batch yield."""
import math

import pytest

from echem_analyzer import acceptance as acc
from echem_analyzer.acceptance import FAIL, MISSING, PASS, Rule
from echem_analyzer.devices import Device
from echem_analyzer.models import Response
from echem_analyzer.presets import KIND_QC, PresetStore


def resp(group, quantity, value, **kw):
    kw.setdefault("unit", "mV")
    return Response(id=f"{group}|{quantity}|{kw.pop('i', 0)}", group=group, quantity=quantity, value=float(value), **kw)


SCREEN = [Rule("ΔEp", maximum=80.0), Rule("ip ratio", 0.9, 1.1)]


def batch():
    return [resp("E1", "ΔEp", 70), resp("E1", "ip ratio", 1.02, unit=""),                  # passes both
            resp("E2", "ΔEp", 95), resp("E2", "ip ratio", 1.00, unit=""),                  # separation too large
            resp("E3", "ΔEp", 60),                                                          # no ratio measured
            resp("E4", "ΔEp", 85), resp("E4", "ip ratio", 1.3, unit="")]                    # fails both


# ===================================================================== rules

def test_rules_are_labelled_from_their_bounds_and_cleaned_when_read_back():
    assert Rule("ΔEp", maximum=80.0).label() == "ΔEp ≤ 80" and Rule("ip ratio", 0.9, 1.1).label() == "0.9 ≤ ip ratio ≤ 1.1"
    assert Rule("Rct", minimum=100.0).label() == "Rct ≥ 100" and Rule("x").label() == "x (no limit)"
    assert Rule("ΔEp", maximum=80.0, name="  Peak separation  ").label() == "Peak separation"
    assert Rule.from_dict({"quantity": "ΔEp", "minimum": 90, "maximum": 10, "how": "banana", "role": None}) == \
        Rule("ΔEp", 10.0, 90.0, "", "", "mean", "")                                        # swapped bounds, unknown mode: repaired
    assert Rule.from_dict({"quantity": "x", "minimum": float("nan"), "maximum": "80", "name": None}) == Rule("x")
    assert Rule.from_dict({"quantity": "  "}) is None and Rule.from_dict("junk") is None and Rule.from_dict({}) is None
    extras: dict = {}
    acc.save(extras, SCREEN)
    assert acc.load(extras) == SCREEN and acc.load({}) == [] and acc.load({"qc_criteria": [None, {"quantity": "a"}, 3]}) == [Rule("a")]


def test_the_starting_template_matches_what_the_cv_analysis_sends():
    (name, rules), = acc.TEMPLATES.items()
    assert "Ferri" in name and [r.quantity for r in rules] == ["ΔEp", "ip ratio"]
    assert rules[0].maximum == 80.0 and (rules[1].minimum, rules[1].maximum) == (0.9, 1.1)


# ===================================================================== evaluation

def test_each_electrode_passes_fails_or_is_incomplete_with_the_reason():
    by = {r.device: r for r in acc.evaluate(batch(), SCREEN)}
    assert [by[k].status for k in ("E1", "E2", "E3", "E4")] == ["pass", "fail", "incomplete", "fail"]
    assert [x.status for x in by["E1"].results] == [PASS, PASS] and by["E1"].results[0].value == 70
    assert [x.status for x in by["E2"].results] == [FAIL, PASS] and [x.rule.quantity for x in by["E2"].failed()] == ["ΔEp"]
    assert [x.status for x in by["E3"].results] == [PASS, MISSING] and by["E3"].results[1].value is None
    assert len(by["E4"].failed()) == 2 and by["E1"].results[0].unit == "mV"


def test_a_fail_outranks_a_missing_value_and_an_electrode_without_responses_is_incomplete():
    rs = [resp("E5", "ΔEp", 120)]                                                           # fails one rule, the other is not measured
    by = {r.device: r for r in acc.evaluate(rs, SCREEN, device_ids=["E5", "E6"])}
    assert by["E5"].status == "fail" and by["E6"].status == "incomplete" and by["E6"].results[0].n == 0
    assert [r.device for r in acc.evaluate(rs, SCREEN, device_ids=["E5", "E6"])] == ["E5", "E6"]
    assert acc.evaluate(rs, [])[0].status == "none"


def test_the_bounds_are_inclusive_and_open_ended_rules_work():
    rs = [resp("A", "q", 80.0), resp("B", "q", 80.0001), resp("C", "q", 0.9), resp("D", "q", 0.8999)]
    assert [r.status for r in acc.evaluate(rs, [Rule("q", maximum=80.0)])][:2] == ["pass", "fail"]
    only_low = {r.device: r.status for r in acc.evaluate(rs, [Rule("q", minimum=0.9)])}
    assert only_low == {"A": "pass", "B": "pass", "C": "pass", "D": "fail"}


def test_several_measurements_are_judged_by_the_mean_by_each_or_by_the_latest():
    rs = [resp("E1", "ΔEp", v, i=i, time=t) for i, (v, t) in enumerate([(70, 1.0), (90, 3.0), (75, 2.0)])]
    mean, each, last = (acc.evaluate(rs, [Rule("ΔEp", maximum=80.0, how=h)])[0] for h in ("mean", "each", "last"))
    assert mean.status == "pass" and mean.results[0].value == pytest.approx(78.3333, abs=1e-3) and mean.results[0].n == 3
    assert each.status == "fail" and each.results[0].value == 90                            # the measurement that failed is the one shown
    assert last.status == "fail" and last.results[0].value == 90                            # latest by time (3.0), not by position
    untimed = [resp("E1", "ΔEp", v, i=i) for i, v in enumerate([70, 90, 75])]
    assert acc.evaluate(untimed, [Rule("ΔEp", maximum=80.0, how="last")])[0].results[0].value == 75      # without times, the last row
    assert acc.evaluate(rs[:1] + rs[2:], [Rule("ΔEp", maximum=80.0, how="each")])[0].results[0].value == pytest.approx(72.5)   # all pass: the mean


def test_condition_and_role_narrow_what_a_rule_tests_and_unused_rows_are_ignored():
    rs = [resp("E1", "Rct", 900, condition="ferricyanide"), resp("E1", "Rct", 5000, condition="buffer"),
          resp("E1", "Rct", 100, condition="ferricyanide", role="blank", i=1), resp("E1", "Rct", 1.0, condition="ferricyanide", use=False, i=2)]
    assert acc.evaluate(rs, [Rule("Rct", maximum=1000.0)])[0].results[0].value == pytest.approx((900 + 5000 + 100) / 3)   # any condition, any role
    assert acc.evaluate(rs, [Rule("Rct", maximum=1000.0, condition="ferricyanide")])[0].results[0].value == pytest.approx(500.0)
    assert acc.evaluate(rs, [Rule("Rct", maximum=1000.0, condition="ferricyanide", role="standard")])[0].results[0].value == 900.0
    assert acc.evaluate(rs, [Rule("Rct", maximum=1000.0, condition="nope")])[0].status == "incomplete"


# ===================================================================== yield

@pytest.mark.parametrize("k,n,lo,hi", [(8, 10, 0.4902, 0.9433), (0, 10, 0.0, 0.2775), (10, 10, 0.7225, 1.0), (1, 2, 0.0945, 0.9055), (80, 100, 0.7112, 0.8666)])
def test_the_wilson_interval_matches_published_values(k, n, lo, hi):
    got = acc.wilson(k, n)
    assert got[0] == pytest.approx(lo, abs=2e-4) and got[1] == pytest.approx(hi, abs=2e-4)
    assert all(math.isnan(v) for v in acc.wilson(0, 0))


def devices():
    return {d.id: d for d in (Device("E1", "thin", "L1"), Device("E2", "thin", "L1"), Device("E3", "thick", "L2"), Device("E4", "thick", "L2"))}


def test_yield_by_variant_and_lot_counts_only_electrodes_that_could_be_judged():
    results = acc.evaluate(batch(), SCREEN)
    rows = {r.key: r for r in acc.batch_yield(results, devices(), "variant")}
    assert list(rows) == ["thick", "thin", "All devices"]
    thin, thick, total = rows["thin"], rows["thick"], rows["All devices"]
    assert (thin.n, thin.passed, thin.failed, thin.incomplete, thin.yield_pct) == (2, 1, 1, 0, 50.0)
    assert (thick.n, thick.passed, thick.failed, thick.incomplete, thick.yield_pct) == (2, 0, 1, 1, 0.0)    # E3 is not counted either way
    assert (total.n, total.passed, total.failed, total.incomplete) == (4, 1, 2, 1) and total.yield_pct == pytest.approx(100 / 3)
    assert thin.lo_pct < 50.0 < thin.hi_pct and thick.lo_pct == 0.0
    assert [r.key for r in acc.batch_yield(results, devices(), "lot")] == ["L1", "L2", "All devices"]


def test_electrodes_without_a_variant_are_grouped_and_listed_last_and_nothing_judged_gives_no_yield():
    reg = devices()
    reg["E1"].variant = ""
    rows = acc.batch_yield(acc.evaluate(batch(), SCREEN), reg, "variant")
    assert [r.key for r in rows] == ["thick", "thin", "(not set)", "All devices"]                # "(not set)" after the named ones, the total last
    assert [r.n for r in rows] == [2, 1, 1, 4]
    unmeasured = acc.batch_yield(acc.evaluate([], SCREEN, device_ids=["E1", "E2"]), devices(), "variant")
    assert math.isnan(unmeasured[-1].yield_pct) and unmeasured[-1].incomplete == 2 and math.isnan(unmeasured[-1].lo_pct)


# ===================================================================== reusing a set of rules

def test_a_named_set_of_rules_is_kept_in_the_shared_preset_file(tmp_path):
    store = PresetStore(tmp_path / "presets.json")
    store.save(KIND_QC, "Ferri screen", {"rules": [r.to_dict() for r in SCREEN]})
    again = PresetStore(tmp_path / "presets.json")                                         # a new session reads it back
    assert again.names(KIND_QC) == ["Ferri screen"]
    assert [Rule.from_dict(d) for d in again.get(KIND_QC, "Ferri screen")["rules"]] == SCREEN
    assert again.names("ca") == [] and again.delete(KIND_QC, "Ferri screen") and again.names(KIND_QC) == []
    old = tmp_path / "old.json"                                                              # a file from before QC sets existed
    old.write_text('{"version": 1, "ca": {}, "cv": {}}', encoding="utf-8")
    assert PresetStore(old).names(KIND_QC) == [] and not PresetStore(old).last_error
