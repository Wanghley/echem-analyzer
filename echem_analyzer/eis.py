"""Electrochemical impedance: equivalent-circuit models and fitting (no Qt).

Convention: ``zim`` is Im(Z), which is negative for capacitive behaviour; Nyquist plots show ``-zim``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.optimize import least_squares

NAN = float("nan")
R_GAS, F_FARADAY = 8.314462618, 96485.33212

# circuit key -> (label, parameter names, bounds (lo, hi) per parameter)
CIRCUITS: dict[str, tuple[str, list[str], list[tuple[float, float]]]] = {
    "rc": ("Rs + (Rct ∥ C)", ["Rs", "Rct", "C"], [(0, np.inf), (0, np.inf), (1e-15, np.inf)]),
    "randles": ("Rs + (Rct ∥ CPE)", ["Rs", "Rct", "Q", "n"],
                [(0, np.inf), (0, np.inf), (1e-15, np.inf), (0.4, 1.0)]),
    "randles_w": ("Rs + ((Rct + W) ∥ CPE)", ["Rs", "Rct", "Q", "n", "sigma"],
                  [(0, np.inf), (0, np.inf), (1e-15, np.inf), (0.4, 1.0), (0, np.inf)]),
}


def impedance(circuit: str, p: dict[str, float], f: np.ndarray) -> np.ndarray:
    """Complex impedance of a circuit at frequencies ``f`` (Hz)."""
    w = 2 * np.pi * np.asarray(f, float)
    jw = 1j * w
    if circuit == "rc":
        return p["Rs"] + 1.0 / (1.0 / p["Rct"] + jw * p["C"])
    zcpe_inv = p["Q"] * jw ** p["n"]
    if circuit == "randles":
        return p["Rs"] + 1.0 / (1.0 / p["Rct"] + zcpe_inv)
    if circuit == "randles_w":
        zw = p["sigma"] / np.sqrt(w) * (1 - 1j)              # semi-infinite Warburg
        return p["Rs"] + 1.0 / (1.0 / (p["Rct"] + zw) + zcpe_inv)
    raise ValueError(f"unknown circuit {circuit!r}")


@dataclass
class EISFit:
    circuit: str
    params: dict[str, float]
    se: dict[str, float]
    f: np.ndarray
    z: np.ndarray                      # measured complex impedance used in the fit
    z_fit: np.ndarray
    rmse_rel: float = NAN              # RMS of |Z_meas - Z_fit| / |Z_meas|
    chi2: float = NAN                  # sum of squared relative residuals
    converged: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def c_eff(self) -> float:
        """Effective capacitance from the CPE of a parallel R-CPE (Brug): (Q·Rct^(1-n))^(1/n)."""
        p = self.params
        if "C" in p:
            return p["C"]
        if "Q" in p and p.get("Rct", 0) > 0:
            return float((p["Q"] * p["Rct"] ** (1.0 - p["n"])) ** (1.0 / p["n"]))
        return NAN


def _guess(f: np.ndarray, z: np.ndarray) -> dict[str, float]:
    order = np.argsort(f)[::-1]
    zre, zim = z.real[order], z.imag[order]
    rs = float(max(np.min(zre[: max(3, len(f) // 8)]), 0.0))
    rtot = float(zre[-1]) if zre[-1] > rs else float(np.max(zre))
    rct = max(rtot - rs, 1e-3 * max(rtot, 1.0))
    k = int(np.argmax(-zim))                                  # top of the semicircle: ω·R·C = 1
    w_top = 2 * np.pi * f[order][k]
    c = 1.0 / (w_top * rct) if w_top > 0 else 1e-6
    return {"Rs": rs, "Rct": rct, "C": c, "Q": c, "n": 0.9, "sigma": max(rct * 0.02, 1e-3)}


def fit_circuit(f, zre, zim, circuit: str = "randles", fmin: float = 0.0, fmax: float = np.inf,
                guess: Optional[dict[str, float]] = None) -> EISFit:
    """Least-squares fit of a circuit to a spectrum with relative (modulus-weighted) residuals."""
    f = np.asarray(f, float)
    z = np.asarray(zre, float) + 1j * np.asarray(zim, float)
    ok = np.isfinite(f) & np.isfinite(z.real) & np.isfinite(z.imag) & (f >= fmin) & (f <= fmax) & (f > 0)
    f, z = f[ok], z[ok]
    if circuit not in CIRCUITS:
        raise ValueError(f"unknown circuit {circuit!r}")
    label, names, bnds = CIRCUITS[circuit]
    out = EISFit(circuit, {n: NAN for n in names}, {n: NAN for n in names}, f, z, np.full_like(z, np.nan))
    if len(f) < len(names) + 2:
        out.warnings.append(f"Need at least {len(names) + 2} frequencies for this circuit.")
        return out
    g = _guess(f, z)
    if guess:
        g.update(guess)
    lo = np.array([b[0] for b in bnds], float)
    hi = np.array([b[1] for b in bnds], float)
    scale = np.array([max(abs(g[n]), 1e-12) for n in names])
    mod = np.abs(z)

    def resid(theta):
        p = dict(zip(names, theta * scale))
        zf = impedance(circuit, p, f)
        d = (z - zf) / mod
        return np.r_[d.real, d.imag]

    best = None
    starts = [1.0, 0.3, 3.0]                                    # multiplicative restarts around the guess
    for m in starts:
        x0 = np.clip(np.array([g[n] for n in names]) * (m if m != 1.0 else 1.0), lo + 1e-18, np.where(np.isfinite(hi), hi, 1e18)) / scale
        if "n" in names:
            x0[names.index("n")] = min(max(g["n"], lo[names.index("n")] / scale[names.index("n")] + 1e-6), 1.0 / scale[names.index("n")])
        try:
            r = least_squares(resid, x0, bounds=(lo / scale, hi / scale), x_scale=1.0, max_nfev=2000)
        except ValueError:
            continue
        if best is None or r.cost < best.cost:
            best = r
    if best is None:
        out.warnings.append("The fit could not be started.")
        return out
    p = dict(zip(names, best.x * scale))
    out.params = {k: float(v) for k, v in p.items()}
    out.z_fit = impedance(circuit, p, f)
    out.chi2 = float(2 * best.cost)
    out.rmse_rel = float(np.sqrt(np.mean(np.abs(z - out.z_fit) ** 2 / mod ** 2)))
    out.converged = bool(best.success)
    dof = max(2 * len(f) - len(names), 1)
    try:
        cov = np.linalg.inv(best.jac.T @ best.jac) * (out.chi2 / dof)
        se = np.sqrt(np.clip(np.diag(cov), 0, None)) * scale
        out.se = {n: float(s) for n, s in zip(names, se)}
    except np.linalg.LinAlgError:
        out.warnings.append("The parameter uncertainties could not be estimated (singular fit).")
    if out.rmse_rel > 0.05:
        out.warnings.append(f"Poor fit (RMS relative error {out.rmse_rel * 100:.1f} %): try another circuit or restrict the frequency range.")
    return out


def k0_from_rct(rct: float, area_cm2: float, conc_mM: float, n: int = 1, temp_c: float = 25.0) -> float:
    """Standard heterogeneous rate constant (cm/s) from Rct: k0 = RT / (n² F² A C Rct), C of each redox form."""
    if min(rct, area_cm2, conc_mM) <= 0:
        return NAN
    t = temp_c + 273.15
    c_mol_cm3 = conc_mM * 1e-6                                   # mM = 1e-6 mol/cm³
    return R_GAS * t / (n ** 2 * F_FARADAY ** 2 * area_cm2 * c_mol_cm3 * rct)


def bode(f, z) -> tuple[np.ndarray, np.ndarray]:
    """|Z| and phase (degrees, positive when capacitive) of a complex spectrum."""
    z = np.asarray(z)
    return np.abs(z), -np.degrees(np.angle(z))
