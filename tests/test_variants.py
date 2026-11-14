"""Comparing design variants and finding what drives performance."""
import math

import numpy as np
import pytest

from echem_analyzer import acceptance as acc
from echem_analyzer import variants as vr
from echem_analyzer.devices import Device
from echem_analyzer.models import Response

CONCS = [1, 2, 5, 10, 20.0]


def batch(sens_thin=0.5, sens_thick=1.0, noise=0.05, seed=3):
    """Three electrodes of each variant; each has its own sensitivity (a 6 % spread) and a little noise on every point."""
    rng = np.random.default_rng(seed)
    rs, devs = [], {}
    for variant, base, ink, ids in (("thin", sens_thin, 2.0, ("T1", "T2", "T3")), ("thick", sens_thick, 5.0, ("K1", "K2", "K3"))):
        for k, dev in enumerate(ids):
            s = base * (1 + 0.06 * (k - 1))
            devs[dev] = Device(dev, variant, "L1", params={"ink volume (µL)": ink + 0.1 * k, "anneal": "120 °C"})
            for c in CONCS:
                rs.append(Response(id=f"{dev}|{c}", group=dev, quantity="Peak", unit="µA", value=float(0.2 + s * c + rng.normal(0, noise)),
                                   conc=c, conc_unit="µM", role="standard", source_label=f"{dev} {c} µM"))
    return rs, devs


def test_each_variant_is_described_by_how_its_electrodes_differ():
    rs, devs = batch()
    cmp_ = vr.compare_variants(rs, devs, "Peak")
    thick, thin = cmp_.rows
    assert [r.variant for r in cmp_.rows] == ["thick", "thin"] and cmp_.conc_unit == "µM" and cmp_.sensitivity_unit == "µA/µM"
    assert thin.n_calibrated == 3 and thin.sens_mean == pytest.approx(0.5, rel=0.03) and thick.sens_mean == pytest.approx(1.0, rel=0.03)
    assert thin.sens_rsd == pytest.approx(6.0, abs=2.0) and thin.sens_lo < thin.sens_mean < thin.sens_hi
    assert thin.sens_hi < thick.sens_lo                                                     # the two variants are clearly different
    assert thin.pooled_sensitivity == pytest.approx(0.5, rel=0.05) and thin.pooled_r2 > 0.99 and thin.range_high == 20.0
    assert len(cmp_.per_device) == 6 and {d.variant for d in cmp_.per_device} == {"thin", "thick"} and cmp_.ok
    assert math.isnan(thin.retention) and math.isnan(thin.t90) and math.isnan(thin.yield_pct)    # nothing measured for these
    assert not cmp_.warnings


def test_the_confidence_interval_is_the_t_interval_of_the_electrodes_sensitivities():
    mean, sd, lo, hi = vr.mean_ci([0.9, 1.0, 1.1])
    assert (mean, sd) == pytest.approx((1.0, 0.1)) and hi - mean == pytest.approx(4.303 * 0.1 / math.sqrt(3), rel=1e-3)
    assert vr.mean_ci([2.0])[0] == 2.0 and all(math.isnan(v) for v in vr.mean_ci([2.0])[1:])
    assert all(math.isnan(v) for v in vr.mean_ci([float("nan")]))


def test_response_time_retention_reproducibility_and_yield_join_the_comparison():
    rs, devs = batch()
    for dev in ("T1", "T2", "T3"):
        rs.append(Response(id=f"{dev}|t90", group=dev, quantity="t90", unit="s", value={"T1": 8.0, "T2": 10.0, "T3": 12.0}[dev]))
    for t, factor in ((0, 1.0), (7, 0.9)):                                                 # the thin variant loses 10 % in a week
        for dev in ("T1", "T2", "T3"):
            rs.append(Response(id=f"{dev}|s{t}", group=dev, quantity="Peak", unit="µA", value=100.0 * factor, conc=10.0, conc_unit="µM",
                               role="standard", time=float(t), condition="analyte"))
    rules = [acc.Rule("Peak", minimum=3.0)]
    cmp_ = vr.compare_variants(rs, devs, "Peak", rules=rules)
    thick, thin = cmp_.rows
    assert thin.t90 == 10.0 and math.isnan(thick.t90)
    assert 3.0 < thin.repro_rsd < 9.0 and 3.0 < thick.repro_rsd < 9.0                           # the 6 % spread between electrodes
    assert thin.n_judged == 3 and math.isfinite(thin.yield_pct) and thin.yield_lo <= thin.yield_pct <= thin.yield_hi


def test_a_fabrication_parameter_is_set_against_the_electrodes_sensitivity():
    rs, devs = batch()
    cmp_ = vr.compare_variants(rs, devs, "Peak")
    t = vr.trend(cmp_, devs, "ink volume (µL)", "sensitivity")
    assert t.ok and len(t.x) == 6 and t.r > 0.97 and t.p < 0.01
    assert t.slope == pytest.approx((1.0 - 0.5) / 3.0, rel=0.1)                             # +0.5 µA/µM for +3 µL of ink
    lod = vr.trend(cmp_, devs, "ink volume (µL)", "lod")
    assert lod.ok and lod.slope < 0                                                         # a more sensitive electrode detects less
    assert not vr.trend(cmp_, devs, "anneal", "sensitivity").ok and len(vr.trend(cmp_, devs, "anneal").x) == 0   # text is not a trend
    fewer = {k: v for k, v in devs.items() if k in ("T1", "K1")}
    two = vr.trend(cmp_, fewer, "ink volume (µL)")
    assert len(two.x) == 2 and not two.ok and math.isnan(two.p)                             # two points: no trend, and no pretence


def test_what_the_comparison_warns_about():
    rs, devs = batch()
    only_thin = {k: v for k, v in devs.items() if v.variant == "thin"}
    cmp_ = vr.compare_variants([r for r in rs if r.group in only_thin], only_thin, "Peak")
    assert any("Only one variant" in w for w in cmp_.warnings)
    unlabelled = vr.compare_variants(rs, {}, "Peak")                                         # no registry: every electrode is "(not set)"
    assert [r.variant for r in unlabelled.rows] == ["(not set)"] and sorted(unlabelled.rows[0].devices) == ["K1", "K2", "K3", "T1", "T2", "T3"]
    assert any("Only one variant" in w for w in unlabelled.warnings)
    none = vr.compare_variants([Response(id="x", group="E1", quantity="Peak", value=1.0, role="standard")], {}, "Peak")
    assert not none.ok and any("two or more" in w or "two or more standards" in w for w in none.warnings)
    one = vr.compare_variants([r for r in rs if r.group in ("T1", "K1")], {k: devs[k] for k in ("T1", "K1")}, "Peak")
    assert any("one calibrated electrode" in w for w in one.warnings) and math.isnan(one.rows[0].sens_lo)


def test_the_variants_tab_compares_plots_and_follows_the_devices_tab(qapp, tmp_path):
    from helpers import make_window
    from echem_analyzer import devices as dv
    win = make_window(tmp_path / "w")
    rs, devs = batch()
    win.session.responses[:] = rs
    dv.save(win.session.extras, devs)
    win.session.responses_changed.emit()
    st = win.study_tab
    assert st.tabs.tabText(st.tabs.count() - 1) == "Variants"
    panel = st.variants
    assert [c.label.text() for c in panel.kpis if not c.isHidden()] == ["VARIANTS", "MOST SENSITIVE", "LOWEST LOD", "BEST YIELD"]
    assert panel.kpis[0].value.text() == "2" and panel.kpis[1].value.text() == "thick" and panel.table.rowCount() == 2
    assert panel.table.item(0, 0).text() == "thick" and "(" in panel.table.item(0, 2).text()                # the mean with its interval
    assert [panel.param.itemText(i) for i in range(panel.param.count())] == ["(none)", "ink volume (µL)"]
    panel.param.setCurrentIndex(1)
    assert panel.trend.ok and len(panel.plot.fig.axes) == 2 and win.session.extras["study"]["variants"]["param"] == "ink volume (µL)"
    panel.metric.setCurrentIndex(1)
    assert panel.trend.metric == "lod" and panel.plot.fig.axes[0].get_ylabel().startswith("Detection limit")
    win.session.responses[:] = []                                                                         # nothing to compare
    win.session.responses_changed.emit()
    assert panel.table.rowCount() == 0 and panel.kpis[0].value.text() == "—"
