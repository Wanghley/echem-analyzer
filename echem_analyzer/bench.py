"""Bench arithmetic for preparing and planning measurements (no Qt).

Small, exact calculations that are done on paper or in a spreadsheet at the bench: dilutions, a serial dilution, the pipette
volumes of a standard-addition series, the current to expect from a redox couple and the instrument range to set for it,
the Nernstian slope of an ion-selective electrode, ionic strength and the Debye length, and the mass to weigh out.

Functions raise :class:`BenchError` with a plain sentence when the inputs cannot be right (a negative volume, a target
above the stock).  Concentrations are in whatever unit the caller uses, as long as it is the same throughout, except where a
function says otherwise; volumes of additions are in µL and the cell volume in mL, as everywhere else in the app.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .calibration import nernst_slope
from .electrode import randles_sevcik_slope
from .potentiometry import relative_permittivity_water
from .study_analysis import CONC_FACTORS

K_B, N_A, E_CHARGE, EPS_0 = 1.380649e-23, 6.02214076e23, 1.602176634e-19, 8.8541878128e-12
CURRENT_RANGES_UA = (1e-3, 1e-2, 1e-1, 1.0, 10.0, 100.0, 1e3, 1e4, 1e5)           # 1 nA ... 100 mA: decade ranges


class BenchError(ValueError):
    """The inputs of a calculator cannot be right; the message says why in plain words."""


def _positive(name: str, value: Optional[float]) -> float:
    if value is None or not math.isfinite(value) or value <= 0:
        raise BenchError(f"{name} must be a positive number.")
    return float(value)


# --------------------------------------------------------------------------- dilutions

@dataclass(frozen=True)
class Dilution:
    c1: float            # concentration of what you take
    v1: float            # volume you take
    c2: float            # concentration you end up with
    v2: float            # final volume
    diluent: float       # volume of solvent to add (v2 − v1)
    factor: float        # c1 / c2


def solve_dilution(c1: Optional[float] = None, v1: Optional[float] = None, c2: Optional[float] = None,
                   v2: Optional[float] = None) -> Dilution:
    """C₁·V₁ = C₂·V₂: leave exactly one of the four as None and it is worked out."""
    given = {"c1": c1, "v1": v1, "c2": c2, "v2": v2}
    missing = [k for k, v in given.items() if v is None]
    if len(missing) != 1:
        raise BenchError("Leave exactly one of the four boxes empty: that is the one to calculate.")
    for k, v in given.items():
        if v is not None:
            _positive({"c1": "Starting concentration", "v1": "Volume taken", "c2": "Final concentration", "v2": "Final volume"}[k], v)
    gone = missing[0]
    if gone == "c1":
        c1 = c2 * v2 / v1
    elif gone == "v1":
        v1 = c2 * v2 / c1
    elif gone == "c2":
        c2 = c1 * v1 / v2
    else:
        v2 = c1 * v1 / c2
    if v1 > v2 * (1 + 1e-12) or c2 > c1 * (1 + 1e-12):
        raise BenchError("That is a concentration, not a dilution: the final concentration cannot exceed the starting one "
                         "and the volume taken cannot exceed the final volume.")
    return Dilution(c1, v1, c2, v2, max(v2 - v1, 0.0), c1 / c2)


@dataclass(frozen=True)
class SerialStep:
    tube: int
    conc: float
    take: float          # volume moved in (from the stock for tube 1, from the previous tube after that)
    diluent: float       # solvent already in the tube


def serial_dilution(c_stock: float, factor: float, steps: int, v_final: float) -> list[SerialStep]:
    """A serial dilution by a constant factor: each tube ends at ``v_final`` and receives ``v_final/factor`` from the one before."""
    _positive("Stock concentration", c_stock)
    _positive("Final volume per tube", v_final)
    if not (math.isfinite(factor) and factor > 1):
        raise BenchError("The dilution factor must be greater than 1 (10 means 1 part in 10).")
    if not (1 <= steps <= 30):
        raise BenchError("Use between 1 and 30 tubes.")
    take = v_final / factor
    return [SerialStep(k, c_stock / factor ** k, take, v_final - take) for k in range(1, steps + 1)]


# --------------------------------------------------------------------------- standard addition

@dataclass(frozen=True)
class Addition:
    step: int
    target: float            # concentration wanted in the cell after this addition
    add_ul: float            # volume of stock to pipette now
    cumulative_ul: float
    actual: float            # concentration really reached (differs from target when volumes are rounded)
    matrix_pct: float        # share of the original cell solution that is left (100 = undiluted)


@dataclass
class AdditionPlan:
    stock: float
    v0_ml: float
    steps: list[Addition] = field(default_factory=list)
    total_ul: float = 0.0
    total_pct_of_cell: float = 0.0
    warnings: list[str] = field(default_factory=list)


def addition_plan(stock: float, v0_ml: float, targets: Sequence[float], round_to_ul: float = 0.0,
                  min_pipette_ul: float = 0.5, max_total_pct: float = 5.0) -> AdditionPlan:
    """Stock volumes to add to a cell of ``v0_ml`` mL to reach increasing ``targets`` (same unit as the stock).

    Inverts the app's own model, C = c_s·ΣV/(V₀+ΣV), so a plan fed back through the standard-addition analysis gives the targets
    again.  With ``round_to_ul`` > 0 each volume is rounded to what a pipette can set and ``actual`` is recalculated from the
    rounded volumes.  Warnings name the practical problems: a volume below the smallest pipette setting, and additions that
    total more than ``max_total_pct`` % of the cell (the matrix is then diluted enough to matter).
    """
    _positive("Stock concentration", stock)
    _positive("Cell volume", v0_ml)
    targets = [float(t) for t in targets]
    if not targets:
        raise BenchError("Give at least one target concentration.")
    if any(not math.isfinite(t) or t <= 0 for t in targets):
        raise BenchError("Target concentrations must be positive numbers.")
    if any(b <= a for a, b in zip(targets, targets[1:])):
        raise BenchError("Target concentrations must increase from one addition to the next.")
    if targets[-1] >= stock:
        raise BenchError("The highest target must be below the stock concentration: the stock is diluted when it is added.")
    v0_ul = v0_ml * 1000.0
    plan = AdditionPlan(stock=stock, v0_ml=v0_ml)
    cumulative_exact = [t * v0_ul / (stock - t) for t in targets]
    previous = 0.0
    cumulative = 0.0
    for k, (target, exact) in enumerate(zip(targets, cumulative_exact), start=1):
        add = exact - previous
        previous = exact
        if round_to_ul > 0:
            add = round(add / round_to_ul) * round_to_ul
        cumulative += add
        plan.steps.append(Addition(k, target, add, cumulative, stock * cumulative / (v0_ul + cumulative), 100.0 * v0_ul / (v0_ul + cumulative)))
    plan.total_ul = cumulative
    plan.total_pct_of_cell = 100.0 * cumulative / v0_ul
    smallest = min(s.add_ul for s in plan.steps)
    if smallest < min_pipette_ul:
        plan.warnings.append(f"The smallest addition is {smallest:.3g} µL, below the {min_pipette_ul:g} µL a pipette can deliver: "
                             "use a more dilute stock for the low end, or a second stock.")
    if plan.total_pct_of_cell > max_total_pct:
        need = min_stock_for(targets[-1], max_total_pct)
        plan.warnings.append(f"The additions total {plan.total_pct_of_cell:.1f} % of the cell volume, so the matrix is diluted by "
                             f"{100 - plan.steps[-1].matrix_pct:.1f} %: use a stock of at least {need:.3g} (the unit of the stock) "
                             f"to keep it under {max_total_pct:g} %.")
    return plan


def min_stock_for(highest_target: float, max_total_pct: float = 5.0) -> float:
    """Smallest stock concentration for which the additions to reach ``highest_target`` stay within ``max_total_pct`` % of the cell."""
    p = _positive("Allowed volume fraction", max_total_pct) / 100.0
    return highest_target * (1 + p) / p


# --------------------------------------------------------------------------- what to expect from a measurement

def expected_peak_current(n: int, area_cm2: float, conc_mM: float, d_cm2s: float, scan_rate_v_s: float, temp_c: float = 25.0,
                          alpha_n: Optional[float] = None) -> float:
    """Peak current (µA) of a diffusion-controlled couple at a scan rate: Randles–Sevcik.

    Reversible by default; pass ``alpha_n`` (α·nₐ) for a totally irreversible wave.  Reuses
    :func:`~echem_analyzer.electrode.randles_sevcik_slope`, the formula the electrode study fits.
    """
    for name, v in (("Area", area_cm2), ("Concentration", conc_mM), ("Diffusion coefficient", d_cm2s), ("Scan rate", scan_rate_v_s)):
        _positive(name, v)
    if n < 1:
        raise BenchError("The number of electrons must be at least 1.")
    return randles_sevcik_slope(int(n), area_cm2, conc_mM, d_cm2s, temp_c, alpha_n) * math.sqrt(scan_rate_v_s)


@dataclass(frozen=True)
class RangeAdvice:
    expected_ua: float
    range_ua: Optional[float]        # None when even the largest range is too small
    fill_pct: float                  # expected current as a share of the chosen range
    label: str


def current_range_label(range_ua: float) -> str:
    if range_ua >= 1e3:
        return f"{range_ua / 1e3:g} mA"
    return f"{range_ua:g} µA" if range_ua >= 1.0 else f"{range_ua * 1e3:g} nA"


def current_range(expected_ua: float, headroom: float = 1.5, ranges: Sequence[float] = CURRENT_RANGES_UA) -> RangeAdvice:
    """The smallest decade range that holds ``headroom`` × the expected current (room for background and for the peak
    being larger than predicted).  A range that is too large wastes resolution; one that is too small clips the signal."""
    expected = _positive("Expected current", abs(expected_ua))
    wanted = expected * _positive("Headroom", headroom)
    chosen = next((r for r in sorted(ranges) if r >= wanted), None)
    if chosen is None:
        return RangeAdvice(expected, None, float("inf"), "larger than the instrument's biggest range")
    return RangeAdvice(expected, chosen, 100.0 * expected / chosen, current_range_label(chosen))


# --------------------------------------------------------------------------- potentiometry

@dataclass(frozen=True)
class NernstPrediction:
    slope_mv_per_decade: float       # signed: positive for a cation, negative for an anion
    delta_mv: float                  # expected potential change from c_low to c_high
    decades: float
    slope_change_per_kelvin: float   # mV/decade per K: how far the slope moves with temperature


def nernst_prediction(z: int, temp_c: float, c_low: float, c_high: float) -> NernstPrediction:
    """Expected slope of an ion-selective electrode for a species of charge ``z`` (signed) and the response to a concentration step."""
    if z == 0 or int(z) != z:
        raise BenchError("The charge must be a non-zero whole number (+1 for Na⁺, −1 for Cl⁻, +2 for Ca²⁺).")
    _positive("Lower concentration", c_low)
    _positive("Higher concentration", c_high)
    if not (-100 < temp_c < 200):
        raise BenchError("The temperature must be in °C, between −100 and 200.")
    magnitude = nernst_slope(abs(int(z)), temp_c)
    slope = math.copysign(magnitude, z)
    decades = math.log10(c_high / c_low)
    return NernstPrediction(slope, slope * decades, decades, slope / (temp_c + 273.15))          # the slope is proportional to T


# --------------------------------------------------------------------------- ionic strength

def salt_species(conc_m: float, z_cation: int, z_anion: int) -> list[tuple[float, int]]:
    """(concentration, charge) of the ions of a fully dissociated salt, e.g. Na₂SO₄ is (+1, −2): 2 Na⁺ and 1 SO₄²⁻."""
    zc, za = abs(int(z_cation)), abs(int(z_anion))
    if zc < 1 or za < 1:
        raise BenchError("Give the charge of the cation and of the anion (for example 1 and 2 for Na₂SO₄).")
    g = math.gcd(zc, za)
    return [(conc_m * (za // g), zc), (conc_m * (zc // g), -za)]


def ionic_strength(species: Sequence[tuple[float, int]]) -> float:
    """I = ½ Σ cᵢzᵢ² in mol/L, from (concentration in mol/L, charge) of every ion."""
    for c, z in species:
        if c < 0 or not math.isfinite(c):
            raise BenchError("Concentrations cannot be negative.")
    return 0.5 * sum(c * z * z for c, z in species)


def debye_length_nm(ionic_strength_m: float, temp_c: float = 25.0, eps_r: Optional[float] = None) -> float:
    """Debye screening length (nm) of an aqueous electrolyte: how far a charge on a sensor surface is felt.

    λ_D = √(ε₀ε_r k_B T / (2 N_A e² I)) with I in mol/m³.  About 0.96 nm at 0.1 M and 9.6 nm at 1 mM (25 °C); a
    field-effect or label-free sensor cannot see a binding event farther from its surface than a few λ_D.
    """
    _positive("Ionic strength", ionic_strength_m)
    eps = relative_permittivity_water(temp_c) if eps_r is None else _positive("Permittivity", eps_r)
    t = temp_c + 273.15
    return math.sqrt(EPS_0 * eps * K_B * t / (2 * N_A * E_CHARGE ** 2 * ionic_strength_m * 1000.0)) * 1e9


# --------------------------------------------------------------------------- weighing

def mass_to_weigh_mg(conc: float, conc_unit: str, volume_ml: float, mw_g_mol: float) -> float:
    """Milligrams to dissolve to make ``volume_ml`` of a ``conc`` solution of a compound with molar mass ``mw_g_mol``."""
    if conc_unit not in CONC_FACTORS:
        raise BenchError(f"Unknown concentration unit {conc_unit!r}.")
    _positive("Concentration", conc)
    _positive("Volume", volume_ml)
    _positive("Molar mass", mw_g_mol)
    return conc * CONC_FACTORS[conc_unit] * volume_ml * 1e-3 * mw_g_mol * 1e3
