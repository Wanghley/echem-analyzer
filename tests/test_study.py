"""Analyzers and the sensor study, end to end on synthetic data with known answers."""
import math

import numpy as np
import pytest

import synth
from echem_analyzer import analyzers, plots, study_analysis as sa
from echem_analyzer.models import Curve, Response
from echem_analyzer.palette import DARK, LIGHT


def curve(kind, x, y, title="run", extra=None, x_unit="V", y_unit="µA", run=""):
    return Curve(kind=kind, title=title, x=np.asarray(x, float), y=np.asarray(y, float), x_unit=x_unit, y_unit=y_unit,
                 meta={"run_title": run or title}, uid=f"{title}#0", extra=extra or {})


def resp(i, value, conc=None, unit="µM", cond="analyte", group="E1", time=None, role="standard", quantity="Peak height", yu="µA"):
    return Response(id=f"r{i}", quantity=quantity, unit=yu, value=value, conc=conc, conc_unit=unit, condition=cond,
                    group=group, time=time, role=role)


# ===================================================================== analyzers

def test_registry_offers_the_right_analyses_per_technique():
    keys = lambda k: [a.key for a in analyzers.for_kind(k)]                        # noqa: E731
    assert keys("EIS") == ["eis_fit"] and "peak" in keys("DPV") and keys("CA")[0] == "staircase"
    assert set(keys("OCP")) >= {"staircase", "steady"} and "cottrell" in keys("CC")
    assert analyzers.for_kind("OTHER")                                              # generic curves still get something


def test_every_analyzer_has_unique_field_keys_and_valid_defaults():
    for a in analyzers.ANALYZERS.values():
        keys = [f.key for f in a.fields]
        assert len(keys) == len(set(keys)) and a.title and a.kinds
        d = a.defaults()
        assert set(d) == set(keys)
        for f in a.fields:
            if f.kind == "choice":
                assert f.default in [v for _, v in f.choices]
            elif f.kind in ("float", "int"):
                assert f.lo <= f.default <= f.hi, (a.key, f.key)
    a = analyzers.ANALYZERS["peak"]
    assert a.params_for({"baseline": "poly", "bogus": 1})["baseline"] == "poly" and "bogus" not in a.params_for({"bogus": 1})


def test_peak_analyzer_on_a_dpv_trace():
    e, i = synth.dpv_trace(10.0)                                                  # 8 µA peak at 0.2 V on a sloping baseline
    c = curve("DPV", e, i, "DPV 10 µM")
    a = analyzers.ANALYZERS["peak"]
    r = a.run(c, a.params_for({"use_window": True, "win_lo": 0.0, "win_hi": 0.45}))
    assert r.ok and not r.warnings
    q = {s.quantity: s for s in r.responses}
    assert q["Peak height"].value == pytest.approx(8.0, rel=0.05) and q["Peak potential"].value == pytest.approx(0.2, abs=0.005)
    assert [m.label for m in r.metrics] == ["Peak height", "Peak potential", "Width (FWHM)", "Area"]
    assert float(r.metrics[2].value) == pytest.approx(70.6, rel=0.06)             # FWHM in mV: 2.355 · 30 mV


def test_peak_analyzer_adds_sweep_features_for_lsv():
    e = np.linspace(-0.4, 0.4, 400)
    c = curve("LSV", e, 12.0 / (1 + np.exp(-(e - 0.05) / 0.02)) + 0.3, "LSV")
    r = analyzers.ANALYZERS["peak"].run(c, analyzers.ANALYZERS["peak"].defaults())
    rows = {n: v for n, v, _ in r.rows}
    assert float(rows["Half-wave potential E½"]) == pytest.approx(0.05, abs=0.005) and "Limiting current" in {s.quantity for s in r.responses}


def test_eis_analyzer_fits_and_reports_k0():
    f_hz, z = synth.randles_spectrum(rct=1000.0, rs=100.0, noise=0.003)
    c = curve("EIS", z.real, -z.imag, "EIS", extra={"freq": f_hz, "zre": z.real, "zim": z.imag}, x_unit="Ω", y_unit="Ω")
    a = analyzers.ANALYZERS["eis_fit"]
    r = a.run(c, a.params_for({"area_cm2": 0.07, "conc_mM": 5.0}))
    q = {s.quantity: s.value for s in r.responses}
    assert r.ok and q["Rct"] == pytest.approx(1000.0, rel=0.04) and q["Rs"] == pytest.approx(100.0, rel=0.05)
    assert q["k0"] == pytest.approx(8.314462618 * 298.15 / (96485.33212 ** 2 * 0.07 * 5e-6 * q["Rct"]))
    assert r.metrics[3].tone == "success"                                           # tight fit
    no_freq = a.run(curve("EIS", z.real, -z.imag, "EIS-noF"), a.defaults())
    assert not no_freq.ok and "frequency" in no_freq.warnings[0]


def test_steady_analyzer():
    t = np.arange(0, 120, 0.2)
    c = curve("OCP", t, 0.25 + 1e-5 * t + np.random.default_rng(0).normal(0, 2e-4, t.size), "OCP", x_unit="s", y_unit="V")
    a = analyzers.ANALYZERS["steady"]
    r = a.run(c, a.params_for({"window": 30.0}))
    assert r.ok and r.responses[0].value == pytest.approx(0.25 + 1e-5 * 105, abs=5e-4) and r.responses[0].sd == pytest.approx(2e-4, rel=0.3)
    assert not a.run(c, a.params_for({"window": 0.1})).ok


def slow_staircase(tau=0.8, seed=1):
    t = np.arange(0, 100, 0.05)
    y = np.full_like(t, -0.3)
    for ts, step in ((25, -0.5), (50, -0.4), (75, -0.3)):
        m = t >= ts
        y[m] += step * (1 - np.exp(-(t[m] - ts) / tau))                              # first-order rise after each step
    return t, y + np.random.default_rng(seed).normal(0, 0.002, t.size)


def test_staircase_analyzer_finds_plateaus_and_response_times():
    tau = 0.8
    t, y = slow_staircase(tau)
    c = curve("CA", t, y, "CA additions", x_unit="s")
    a = analyzers.ANALYZERS["staircase"]
    r = a.run(c, a.params_for({"drop_last": 0, "window": 5.0, "sharp": "slow"}))
    assert r.ok and r.metrics[0].value == "3"
    plateaus = [s.value for s in r.responses if s.quantity == "Plateau"]
    assert plateaus == pytest.approx([-0.3, -0.8, -1.2, -1.5], abs=0.02)
    deltas = [s.value for s in r.responses if s.quantity == "Step change (Δ)"]
    assert deltas == pytest.approx([-0.5, -0.9, -1.2], abs=0.03)
    t90 = [s.value for s in r.responses if s.quantity == "t90"]
    assert len(t90) == 3 and t90 == pytest.approx([-tau * math.log(0.1)] * 3, rel=0.3)      # measured from the onset
    assert {s.suffix for s in r.responses if s.quantity == "Plateau"} == {"baseline", "step 1", "step 2", "step 3"}
    header, rows = r.table
    assert len(rows) == 4 and header[0] == "Segment"
    assert not a.run(curve("CA", t, np.zeros_like(t) - 1, "flat"), a.defaults()).ok


def test_slow_steps_need_the_right_shape_setting_for_each_technique():
    """Sharp-step detection (which rejects smooth decays) must not reject a potentiometric sensor's slow response."""
    t, y = slow_staircase(1.5)
    a = analyzers.ANALYZERS["staircase"]
    base = {"drop_last": 0, "window": 5.0}
    assert not a.run(curve("CA", t, y, "ca", x_unit="s"), a.params_for(base)).ok                # CA: auto = sharp only
    assert a.run(curve("CA", t, y, "ca", x_unit="s"), a.params_for({**base, "sharp": "slow"})).ok
    assert a.run(curve("OCP", t, y, "ocp", x_unit="s", y_unit="V"), a.params_for(base)).ok       # OCP: auto allows slow
    assert not a.run(curve("OCP", t, y, "ocp", x_unit="s", y_unit="V"), a.params_for({**base, "sharp": "sharp"})).ok


def test_a_smooth_decay_is_still_not_a_staircase():
    t = np.arange(0, 100, 0.05)
    c = curve("CA", t, -0.25 - 1.3 * np.exp(-t / 8.0), "decay", x_unit="s")
    assert not analyzers.ANALYZERS["staircase"].run(c, analyzers.ANALYZERS["staircase"].defaults()).ok


def test_cottrell_and_anson_analyzers():
    k = 96485.33212 * 0.07 * 5e-6 * math.sqrt(7.6e-6 / math.pi) * 1e6
    t = np.arange(0.05, 12, 0.05)
    a = analyzers.ANALYZERS["cottrell"]
    p = a.params_for({"t0": 0.5, "t1": 10.0, "area_cm2": 0.07, "conc_mM": 5.0})
    r = a.run(curve("CA", t, -k / np.sqrt(t), "CA", x_unit="s"), p)
    assert r.ok and {s.quantity: s.value for s in r.responses}["Diffusion coefficient D"] == pytest.approx(7.6e-6, rel=1e-3)
    rc = a.run(curve("CC", t, 2 * k * np.sqrt(t) + 3.0, "CC", x_unit="s", y_unit="µC"), p)
    assert rc.ok and {s.quantity: s.value for s in rc.responses}["Diffusion coefficient D"] == pytest.approx(7.6e-6, rel=1e-3)
    assert any("Γ" == m.label for m in rc.metrics)


@pytest.mark.parametrize("pal", [LIGHT, DARK], ids=["light", "dark"])
def test_every_analyzer_draws_in_both_palettes(pal):
    e, i = synth.dpv_trace(10.0)
    f_hz, z = synth.randles_spectrum()
    t = np.arange(0, 100, 0.1)
    cases = [("peak", curve("DPV", e, i, "DPV")),
             ("eis_fit", curve("EIS", z.real, -z.imag, "EIS", extra={"freq": f_hz, "zre": z.real, "zim": z.imag})),
             ("steady", curve("OCP", t, 0.2 + 0 * t + 0.001 * np.sin(t), "OCP", x_unit="s", y_unit="V")),
             ("staircase", curve("CA", t, -np.floor(t / 25) - 0.3, "CA", x_unit="s")),
             ("cottrell", curve("CA", t[1:], -3 / np.sqrt(t[1:]), "CA", x_unit="s"))]
    for key, c in cases:
        a = analyzers.ANALYZERS[key]
        params = a.params_for({"drop_last": 0, "t1": 20.0} if key in ("steady", "staircase", "cottrell") else {})
        res = a.run(c, params)
        png = plots.render_png(lambda fig, p: a.draw(fig, c, params, res, p), a.figsize, palette=pal)
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and plots.png_size(png)[0] > 300, key


def test_a_failed_analysis_still_draws_a_message_not_an_error():
    a = analyzers.ANALYZERS["peak"]
    c = curve("DPV", [0, 1, 2], [1, 2, 3], "tiny")
    res = a.run(c, a.defaults())
    assert not res.ok
    assert plots.render_png(lambda fig, p: a.draw(fig, c, a.defaults(), res, p), a.figsize)[:4] == b"\x89PNG"


def test_guess_concentration_from_titles():
    assert analyzers.guess_conc(curve("DPV", [1], [1], "x", run="sensor B7 10 µM")) == (10.0, "µM")
    assert analyzers.guess_conc(curve("DPV", [1], [1], "50 nM glucose")) == (50.0, "nM")
    assert analyzers.guess_conc(curve("DPV", [1], [1], "blank")) is None


# ===================================================================== study: calibration

def calibration_responses(group="E1", slope=0.8, noise=0.0, seed=0, concs=(1, 2, 5, 10, 20, 50), reps=1, t=None):
    rng = np.random.default_rng(seed)
    out, k = [], 0
    for c in concs:
        for _ in range(reps):
            out.append(resp(f"{group}{k}", slope * c + rng.normal(0, noise), conc=c, group=group, time=t))
            k += 1
    return out


def test_calibration_end_to_end_from_raw_dpv_traces():
    """Synthetic DPV scans at known concentrations -> analyzer -> responses -> calibration -> LOD."""
    a = analyzers.ANALYZERS["peak"]
    params = a.params_for({"use_window": True, "win_lo": 0.0, "win_hi": 0.45})
    rs = []
    for k, c in enumerate([0.0, 1, 2, 5, 10, 20]):
        e, i = synth.dpv_trace(c)
        i = i + np.random.default_rng(k).normal(0, 0.01, i.size)
        height = a.run(curve("DPV", e, i, f"DPV {c} µM"), params).responses[0].value
        rs.append(resp(k, height, conc=c if c else None, role="standard" if c else "blank"))
        if not c:
            rs.append(resp(f"b{k}", height * 1.0 + 0.0, conc=0.0, role="standard"))                   # the zero point
    for j in range(4):                                                                              # extra blanks for σ
        e, i = synth.dpv_trace(0.0)
        rs.append(resp(f"bb{j}", a.run(curve("DPV", e, i + np.random.default_rng(100 + j).normal(0, 0.01, i.size), "blank"), params).responses[0].value,
                       role="blank"))
    cs = sa.run_calibration(rs, sa.CalibrationOptions(quantity="Peak height", model="linear", restrict_range=False))
    assert cs.ok and cs.model == "linear" and cs.fom.sigma_source.startswith("blank")
    assert cs.sensitivity == pytest.approx(0.8, rel=0.03) and cs.fom.r2 > 0.999
    assert cs.lod == pytest.approx(3 * np.std(cs.blanks, ddof=1) / cs.sensitivity) and 0 < cs.lod < 0.5
    assert cs.sensitivity_unit == "µA/µM"


def test_calibration_picks_the_model_and_reports_each_kind_of_sensitivity():
    nonlin = [resp(i, 80.0 * c / (25.0 + c), conc=c) for i, c in enumerate([1, 2, 5, 10, 20, 50, 100, 200])]
    cs = sa.run_calibration(nonlin, sa.CalibrationOptions(quantity="Peak height"))
    assert cs.model in ("michaelis", "hill")
    mm = sa.run_calibration(nonlin, sa.CalibrationOptions(quantity="Peak height", model="michaelis"))
    assert mm.sensitivity == pytest.approx(80.0 / 25.0, rel=0.02) and "initial slope" in mm.sensitivity_unit
    pot = [resp(i, 250.0 + 59.16 * math.log10(c * 1e-6), conc=c, quantity="E", yu="mV") for i, c in enumerate([0.1, 1, 10, 100, 1000])]
    lg = sa.run_calibration(pot, sa.CalibrationOptions(quantity="E", model="loglinear"))
    assert lg.sensitivity == pytest.approx(59.16, abs=0.05) and lg.sensitivity_unit == "mV/decade" and any("Nernstian" in w for w in lg.warnings)


def test_linear_range_limits_the_sensitivity_for_a_saturating_sensor():
    cs_all = [resp(i, 2.0 * c if c <= 20 else 40 + 0.1 * (c - 20), conc=c) for i, c in enumerate([1, 2, 5, 10, 20, 50, 100, 200])]
    on = sa.run_calibration(cs_all, sa.CalibrationOptions(quantity="Peak height", model="linear", restrict_range=True))
    off = sa.run_calibration(cs_all, sa.CalibrationOptions(quantity="Peak height", model="linear", restrict_range=False))
    assert on.sensitivity == pytest.approx(2.0, rel=0.02) and on.fom.range_high == 20
    assert off.sensitivity < 1.0 and on.fom.n_range == 5


def test_units_are_converted_to_a_common_scale():
    mixed = [resp(0, 10.0, conc=1.0, unit="µM"), resp(1, 20.0, conc=2000.0, unit="nM"), resp(2, 40.0, conc=0.004, unit="mM")]
    cs = sa.run_calibration(mixed, sa.CalibrationOptions(quantity="Peak height", model="linear", conc_unit="µM"))
    assert sorted(cs.x) == pytest.approx([1.0, 2.0, 4.0]) and cs.sensitivity == pytest.approx(10.0)
    assert sa.conc_to(1.0, "mM", "µM") == pytest.approx(1000.0)
    with pytest.raises(ValueError):
        sa.conc_to(1.0, "furlongs", "µM")


def test_samples_are_quantified_and_recovery_is_computed():
    std = [resp(i, 0.8 * c, conc=c) for i, c in enumerate([1, 2, 5, 10, 20])]
    samples = [resp("s1", 0.8 * 9.5, conc=10.0, role="sample"), resp("s2", 0.8 * 10.5, conc=10.0, role="sample"),
               resp("s3", 0.8 * 7.0, conc=None, role="sample")]
    cs = sa.run_calibration(std + samples, sa.CalibrationOptions(quantity="Peak height", model="linear"))
    assert [round(s.conc, 3) for s in cs.samples] == [9.5, 10.5, 7.0]
    assert [round(s.recovery_pct, 1) for s in cs.samples[:2]] == [95.0, 105.0] and math.isnan(cs.samples[2].recovery_pct)
    assert cs.recovery.mean_pct == pytest.approx(100.0) and all(s.se >= 0 for s in cs.samples)
    out_of_range = sa.run_calibration([resp(i, 80.0 * c / (25 + c), conc=c) for i, c in enumerate([1, 2, 5, 10, 20, 50, 100, 200])]
                                      + [resp("x", 95.0, role="sample")], sa.CalibrationOptions(quantity="Peak height", model="michaelis"))
    assert math.isnan(out_of_range.samples[0].conc) and any("outside" in w for w in out_of_range.warnings)


def test_calibration_degenerate_inputs_explain_themselves():
    assert sa.run_calibration([], sa.CalibrationOptions(quantity="x")).warnings
    one = sa.run_calibration([resp(0, 1, conc=5), resp(1, 2, conc=5)], sa.CalibrationOptions(quantity="Peak height"))
    assert not one.ok and "different concentrations" in one.warnings[0]
    unused = [resp(i, 0.8 * c, conc=c) for i, c in enumerate([1, 2, 5, 10])]
    unused[3].use = False
    assert len(sa.run_calibration(unused, sa.CalibrationOptions(quantity="Peak height", model="linear")).x) == 3
    pooled = sa.run_calibration(calibration_responses("E1") + calibration_responses("E2", slope=0.7) + [resp("s", 5.0, role="sample")],
                                sa.CalibrationOptions(quantity="Peak height", model="linear"))
    assert any("pooled" in w for w in pooled.warnings)
    assert sa.run_calibration(calibration_responses("E1") + calibration_responses("E2", slope=0.7),
                              sa.CalibrationOptions(quantity="Peak height", group="E2", model="linear")).sensitivity == pytest.approx(0.7)


@pytest.mark.parametrize("pal", [LIGHT, DARK], ids=["light", "dark"])
def test_study_plots_render(pal):
    reps = calibration_responses("E1", reps=3, noise=0.05) + calibration_responses("E2", slope=0.75, reps=3, noise=0.05, seed=1)
    cs = sa.run_calibration(reps + [resp("blank", 0.02, role="blank"), resp("blank2", -0.01, role="blank"), resp("blank3", 0.03, role="blank")],
                            sa.CalibrationOptions(quantity="Peak height", model="linear"))
    rep = sa.run_reproducibility(reps, "Peak height")
    stab = sa.run_stability(calibration_responses("E1", concs=(10,), t=0)[:1] + [resp(5, 7.5, conc=10, time=10), resp(6, 6.0, conc=10, time=20), resp(7, 5.0, conc=10, time=30)],
                            "Peak height", conc=10.0)
    sel = sa.run_selectivity([resp(0, 8.0, conc=10), resp(1, 1.0, conc=10, cond="ascorbate"), resp(2, 0.4, conc=10, cond="urea")], "Peak height")
    r = lambda draw: plots.render_png(draw, (7, 4), palette=pal)[:4]                                 # noqa: E731
    assert r(lambda fig, p: plots.study_calibration(fig, cs, p)) == b"\x89PNG"
    assert r(lambda fig, p: plots.study_calibration(fig, cs, p, logx=True)) == b"\x89PNG"
    assert r(lambda fig, p: plots.study_reproducibility(fig, rep, p)) == b"\x89PNG"
    assert r(lambda fig, p: plots.study_stability(fig, stab, p)) == b"\x89PNG"
    assert r(lambda fig, p: plots.study_selectivity(fig, sel, p)) == b"\x89PNG"
    assert r(lambda fig, p: plots.study_recovery(fig, cs, p)) == b"\x89PNG"
    empty = sa.run_calibration([], sa.CalibrationOptions(quantity="x"))
    assert r(lambda fig, p: plots.study_calibration(fig, empty, p)) == b"\x89PNG"
    assert r(lambda fig, p: plots.study_calibration(fig, None, p)) == b"\x89PNG"


# ===================================================================== study: reproducibility, stability, selectivity

def test_reproducibility_across_electrodes():
    rs = []
    for g, s in (("E1", 0.80), ("E2", 0.82), ("E3", 0.78), ("E4", 0.80)):
        rs += calibration_responses(g, slope=s, concs=(2, 5, 10, 20))
    r = sa.run_reproducibility(rs, "Peak height")
    assert [row.conc for row in r.rows] == [2, 5, 10, 20] and all(row.n_groups == 4 for row in r.rows)
    row10 = r.rows[2]
    assert row10.stats.mean == pytest.approx(10 * np.mean([0.80, 0.82, 0.78, 0.80])) and row10.stats.rsd_pct == pytest.approx(
        np.std([8.0, 8.2, 7.8, 8.0], ddof=1) / 8.0 * 100)
    assert r.sensitivity["E2"] == pytest.approx(0.82) and r.sensitivity_stats.n == 4
    assert r.sensitivity_stats.rsd_pct == pytest.approx(np.std([0.8, 0.82, 0.78, 0.8], ddof=1) / 0.8 * 100)
    outlier = rs + [resp("o", 30.0, conc=10, group="E5")] + [resp("o2", 9.0, conc=10, group="E6")]
    assert "E5" in sa.run_reproducibility(outlier, "Peak height").rows[2].outliers
    assert sa.run_reproducibility(calibration_responses("E1"), "Peak height").warnings                    # one electrode only


def test_stability_from_responses_over_time():
    rs = [resp(i, 10.0 * math.exp(-t / 30.0), conc=10, time=t) for i, t in enumerate([0, 5, 10, 20, 30, 45])]
    st = sa.run_stability(rs, "Peak height", conc=10.0)
    assert st.result.tau == pytest.approx(30.0, rel=1e-3) and st.result.half_life == pytest.approx(30 * math.log(2), rel=1e-3)
    assert st.result.final_retention_pct == pytest.approx(100 * math.exp(-1.5), rel=1e-3)
    assert sa.run_stability([resp(0, 1, conc=1, time=0)], "Peak height").warnings
    assert sa.run_stability(rs, "Peak height", conc=999.0).result is None                                 # no data at that level


def test_selectivity_study():
    rs = [resp(0, 8.0, conc=10), resp(1, 16.0, conc=20), resp(2, 0.8, conc=10, cond="ascorbate"), resp(3, 0.2, conc=10, cond="urea"),
          resp(4, 9.0, conc=10, cond="glucose")]
    s = sa.run_selectivity(rs, "Peak height")
    assert s.analyte_conc == 10 and s.analyte_response == 8.0
    by = {r.interferent: r for r in s.rows}
    assert by["ascorbate"].interference_pct == pytest.approx(10.0) and by["ascorbate"].change_pct == pytest.approx(-90.0)
    assert by["urea"].k_sel == pytest.approx(0.025) and by["glucose"].change_pct == pytest.approx(12.5)
    assert sa.run_selectivity(rs[:2], "Peak height").warnings


def test_helpers_list_what_is_available():
    rs = [resp(0, 1, conc=1, quantity="A"), resp(1, 1, conc=1, quantity="B", cond="x", group="G1")]
    assert sa.quantities(rs) == ["A", "B"] and sa.conditions(rs) == ["analyte", "x"] and sa.groups(rs) == ["E1", "G1"]
    rs[0].use = False
    assert sa.quantities(rs) == ["B"] and sa.pick_unit([resp(0, 1, conc=1, unit="nM")]) == "nM" and sa.pick_unit([]) == "µM"


def test_time_selects_one_calibration_session():
    day0 = [resp(f"a{i}", 0.8 * c, conc=c, time=0.0) for i, c in enumerate([1, 2, 5, 10, 20])]
    day7 = [resp(f"b{i}", 0.6 * c, conc=c, time=7.0) for i, c in enumerate([1, 2, 5, 10, 20])]
    untimed = [resp(f"c{i}", 0.7 * c, conc=c) for i, c in enumerate([1, 2, 5, 10, 20])]
    rs = day0 + day7 + untimed
    one = lambda t: sa.run_calibration(rs, sa.CalibrationOptions(quantity="Peak height", model="linear", time=t)).sensitivity   # noqa: E731
    assert one(0.0) == pytest.approx(0.8) and one(7.0) == pytest.approx(0.6) and one(sa.NO_TIME) == pytest.approx(0.7)
    assert 0.6 < one(None) < 0.8                                                                      # all times: smeared together
    assert sa.times(rs) == [0.0, 7.0]
    rep = sa.run_reproducibility(rs, "Peak height", time=0.0)
    assert rep.rows[0].n_total == 1 and rep.sensitivity == {"E1": pytest.approx(0.8)}
