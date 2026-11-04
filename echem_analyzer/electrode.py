"""Electrode characterisation: Randles–Sevcik / ECSA, scan-rate diagnostics, Laviron kinetics, Cdl (no Qt)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np
from scipy import stats

from .calibration import ModelFit, fit_linear

NAN = float("nan")
R_GAS, F_FARADAY = 8.314462618, 96485.33212


def randles_sevcik_slope(n: int, area_cm2: float, conc_mM: float, d_cm2s: float, temp_c: float = 25.0,
                         irreversible_alpha_n: Optional[float] = None) -> float:
    """Theoretical slope of ip (µA) against √ν (V/s)^½.

    Reversible: ip = 0.4463·n·F·A·C·√(n F ν D / R T).  Irreversible: ip = 0.4958·n·F·A·C·√(α n_a F ν D / R T).
    A in cm², C in mM (= 1e-6 mol/cm³), D in cm²/s.
    """
    t = temp_c + 273.15
    c = conc_mM * 1e-6
    if irreversible_alpha_n is None:
        coef, an = 0.4463, n
    else:
        coef, an = 0.4958, irreversible_alpha_n
    return coef * n * F_FARADAY * area_cm2 * c * math.sqrt(an * F_FARADAY * d_cm2s / (R_GAS * t)) * 1e6


def ecsa_from_slope(slope_ua: float, n: int, conc_mM: float, d_cm2s: float, temp_c: float = 25.0) -> float:
    """Electroactive area (cm²) from the measured ip-vs-√ν slope (reversible Randles–Sevcik)."""
    unit = randles_sevcik_slope(n, 1.0, conc_mM, d_cm2s, temp_c)
    return abs(slope_ua) / unit if unit else NAN


def diffusion_from_slope(slope_ua: float, n: int, area_cm2: float, conc_mM: float, temp_c: float = 25.0) -> float:
    """Diffusion coefficient (cm²/s) from the slope when the area is known (D ∝ slope²)."""
    unit_d1 = randles_sevcik_slope(n, area_cm2, conc_mM, 1.0, temp_c)
    return (abs(slope_ua) / unit_d1) ** 2 if unit_d1 else NAN


@dataclass
class ScanRateAnalysis:
    nu: np.ndarray
    ip_a: np.ndarray
    ip_c: np.ndarray
    ep_a: np.ndarray
    ep_c: np.ndarray
    fit_a: Optional[ModelFit] = None          # ip_a vs sqrt(nu)
    fit_c: Optional[ModelFit] = None
    loglog_a: float = NAN                     # d log|ip| / d log ν (0.5 diffusion, 1.0 adsorption)
    loglog_c: float = NAN
    ecsa_a: float = NAN
    ecsa_c: float = NAN
    roughness: float = NAN
    regime: str = ""
    delta_ep: np.ndarray = field(default_factory=lambda: np.array([]))
    laviron: Optional["LavironResult"] = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class LavironResult:
    alpha: float = NAN
    n: float = NAN
    ks: float = NAN                           # s^-1 for surface-confined species
    ks_sd: float = NAN
    slope_a: float = NAN                      # dEpa / d ln ν (V)
    slope_c: float = NAN
    n_points: int = 0


def laviron(nu, ep_a, ep_c, n_guess: float = 1.0, temp_c: float = 25.0) -> LavironResult:
    """Laviron analysis of a surface-confined couple from the high-scan-rate (ΔEp > 200/n mV) points.

    Ep depends linearly on ln ν there: slope_a = RT/((1-α)nF), slope_c = -RT/(α nF), which gives α and n.
    ks follows from log ks = α log(1-α) + (1-α) log α − log(RT/(nFν)) − α(1-α) nF ΔEp / (2.3 RT).
    """
    nu, ep_a, ep_c = (np.asarray(v, float) for v in (nu, ep_a, ep_c))
    out = LavironResult()
    t = temp_c + 273.15
    rtf = R_GAS * t / F_FARADAY
    sel = (ep_a - ep_c) > 0.2 / max(n_guess, 1)
    if sel.sum() < 3:
        return out
    ln_nu = np.log(nu[sel])
    fa, fc = fit_linear(ln_nu, ep_a[sel]), fit_linear(ln_nu, ep_c[sel])
    if not (fa.ok and fc.ok):
        return out
    out.slope_a, out.slope_c, out.n_points = fa.p("slope"), fc.p("slope"), int(sel.sum())
    if out.slope_a > 0 > out.slope_c:
        one_minus_alpha_n = rtf / out.slope_a
        alpha_n = -rtf / out.slope_c
        out.n = one_minus_alpha_n + alpha_n
        out.alpha = alpha_n / out.n
        a, n = out.alpha, out.n
        dep = ep_a[sel] - ep_c[sel]
        logks = (a * math.log10(1 - a) + (1 - a) * math.log10(a) - np.log10(rtf / (n * nu[sel]))
                 - a * (1 - a) * n * dep / (math.log(10) * rtf))
        ks = 10 ** logks
        out.ks, out.ks_sd = float(np.mean(ks)), float(np.std(ks, ddof=1)) if ks.size > 1 else NAN
    return out


def scan_rate_analysis(nu, ip_a, ip_c, ep_a, ep_c, n: int = 1, conc_mM: float = 0.0, d_cm2s: float = 0.0,
                       area_geo_cm2: float = 0.0, temp_c: float = 25.0) -> ScanRateAnalysis:
    """Randles–Sevcik, log–log exponent, peak separation, ECSA and Laviron kinetics from a scan-rate series."""
    order = np.argsort(np.asarray(nu, float))
    nu, ip_a, ip_c, ep_a, ep_c = (np.asarray(v, float)[order] for v in (nu, ip_a, ip_c, ep_a, ep_c))
    res = ScanRateAnalysis(nu, ip_a, ip_c, ep_a, ep_c, delta_ep=ep_a - ep_c)
    if len(nu) < 3:
        res.warnings.append("A scan-rate study needs at least 3 scan rates.")
        return res
    sq = np.sqrt(nu)
    res.fit_a, res.fit_c = fit_linear(sq, ip_a), fit_linear(sq, ip_c)
    for name, ip in (("a", ip_a), ("c", ip_c)):
        ok = np.isfinite(ip) & (ip != 0) & (nu > 0)
        if ok.sum() >= 3:
            sl = stats.linregress(np.log10(nu[ok]), np.log10(np.abs(ip[ok]))).slope
            setattr(res, f"loglog_{name}", float(sl))
    b = np.nanmean([res.loglog_a, res.loglog_c])
    if math.isfinite(b):
        res.regime = ("diffusion-controlled" if abs(b - 0.5) < 0.12 else
                      "adsorption / surface-confined" if abs(b - 1.0) < 0.15 else
                      "mixed or intermediate control")
    if conc_mM > 0 and d_cm2s > 0:
        if res.fit_a.ok:
            res.ecsa_a = ecsa_from_slope(res.fit_a.p("slope"), n, conc_mM, d_cm2s, temp_c)
        if res.fit_c.ok:
            res.ecsa_c = ecsa_from_slope(res.fit_c.p("slope"), n, conc_mM, d_cm2s, temp_c)
        ecsas = [v for v in (res.ecsa_a, res.ecsa_c) if math.isfinite(v)]
        if ecsas and area_geo_cm2 > 0:
            res.roughness = float(np.mean(ecsas)) / area_geo_cm2
    res.laviron = laviron(nu, ep_a, ep_c, n_guess=n, temp_c=temp_c)
    if res.fit_a.ok and res.fit_a.r2 < 0.98:
        res.warnings.append("ip vs √ν is not linear (R² < 0.98): the process may not be purely diffusion-controlled.")
    return res


def double_layer_capacitance(nu, delta_i_ua, area_cm2: float = 0.0, cs_uf_cm2: float = 0.0) -> dict[str, float]:
    """Cdl from non-faradaic CVs: half the anodic–cathodic current gap at a mid-potential vs ν has slope Cdl.

    ``delta_i_ua`` is (i_anodic − i_cathodic)/2 in µA, ``nu`` in V/s: slope = Cdl in µF.  With an area, µF/cm²;
    with a specific capacitance ``cs_uf_cm2`` (typical 20–60 µF/cm² for a flat metal), the roughness factor.
    """
    f = fit_linear(nu, delta_i_ua)
    out = {"cdl_uF": NAN, "cdl_se": NAN, "r2": NAN, "per_area": NAN, "roughness": NAN}
    if f.ok:
        out["cdl_uF"], out["cdl_se"], out["r2"] = f.p("slope"), f.se("slope"), f.r2
        if area_cm2 > 0:
            out["per_area"] = out["cdl_uF"] / area_cm2
            if cs_uf_cm2 > 0:
                out["roughness"] = out["per_area"] / cs_uf_cm2
    return out
