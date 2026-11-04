"""Plain data containers shared by the I/O, analysis and GUI layers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

import numpy as np

KIND_CA = "CA"
KIND_CV = "CV"
KIND_OTHER = "OTHER"


@dataclass(eq=False)       # identity comparison: value-equality of numpy arrays is meaningless/raises
class Curve:
    """One x/y trace loaded from a PalmSens export."""

    kind: str                      # KIND_CA | KIND_CV | KIND_OTHER
    title: str                     # e.g. "CA i vs t [1]" or "CV i vs E Scan 2"
    x: np.ndarray
    y: np.ndarray
    x_unit: str = ""
    y_unit: str = "µA"
    scan: Optional[int] = None     # CV scan number
    meta: dict[str, Any] = field(default_factory=dict)  # E, dt, timestamp, run title, ...
    source: str = ""               # file path it came from
    uid: str = ""                  # unique id inside the session (set by the loader)
    extra: dict[str, np.ndarray] = field(default_factory=dict)   # more columns, e.g. EIS: freq, zre, zim

    @property
    def label(self) -> str:
        run = self.meta.get("run_title")
        base = self.title if not run else f"{run} · {self.title}"
        return base

    @property
    def n(self) -> int:
        return int(len(self.x))


@dataclass
class DataFile:
    path: Path
    fmt: str                       # "csv" | "pssession"
    curves: list[Curve] = field(default_factory=list)
    exported_at: str = ""

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def stem(self) -> str:
        return self.path.stem

    def of_kind(self, kind: str) -> list[Curve]:
        return [c for c in self.curves if c.kind == kind]


@dataclass
class CAAnalysisSettings:
    """Everything the user can tweak for one CA run (serialisable to JSON)."""

    # preprocessing
    drop_last: int = 1                 # trailing points to discard (end-of-run spike)
    # step detection
    detect_k: float = 8.0              # threshold = k * robust sigma
    detect_min_sep: float = 5.0        # s
    detect_lag: float = 0.5            # s
    expected_steps: int = 0            # 0 = auto
    # response extraction
    method: str = "plateau"            # "plateau" | "drift"
    settle: float = 3.0                # s skipped after each step
    window: float = 5.0                # s of plateau averaged (end of each segment)
    end_guard: float = 0.5             # s excluded just before the next step
    # additions.  Volumes and currents are always stored in mL, µL and µA; the *_unit fields only say how the
    # window shows them (a unit selector converts the number on screen, never the stored quantity).
    stock_conc: float = 0.0
    stock_unit: str = "mM"
    v0_ml: float = 0.0                 # total cell volume before any addition (sample + reagent)
    v_add_ul: float = 0.0              # nominal volume per addition (µL)
    verified_ul: float = 0.0           # volume per addition verified by a pipette check (µL); 0 = not checked
    dilution_mode: str = "concentration"   # "concentration" | "signal" | "none": exactly one correction, never both
    v_sample_ml: float = 0.0           # sample inside V0 (mL); 0 → same as V0 (sample not diluted)
    background: float = 0.0            # µA subtracted from |I| (blank run at the same potential and time)
    area_cm2: float = 0.0
    v0_unit: str = "mL"
    v_add_unit: str = "µL"
    verified_unit: str = "µL"
    v_sample_unit: str = "mL"
    bkg_unit: str = "µA"
    # LOD / LOQ
    sigma_source: str = "fit"          # "fit" (s(y/x)) | "baseline" (noise of one trace) | "blank" (SD of replicates)
    baseline_window: float = 10.0      # s before first step used for the baseline noise
    blank_replicates: list[float] = field(default_factory=list)   # µA: blank replicate currents (SD = σ_blank)
    lod_k: float = 3.3                 # LOD = lod_k·σ/|b|
    loq_k: float = 10.0                # LOQ = loq_k·σ/|b|
    suitability_min: float = 12.0      # b·√Sxx / s(y) below this: the extrapolation is not trustworthy
    plausible_max: float = 0.0         # largest believable sample concentration in the stock's unit; 0 = no check
    compare_baseline: bool = False     # also show the result with the baseline point switched the other way
    # per-step data
    ignored_events: list[float] = field(default_factory=list)   # opposite-direction jumps (not additions)
    step_times: list[float] = field(default_factory=list)
    step_volumes_ul: list[float] = field(default_factory=list)   # len == len(step_times)
    step_conc_override: list[Optional[float]] = field(default_factory=list)
    step_included: list[bool] = field(default_factory=list)
    include_baseline: bool = True      # k = 0 point used in the standard-addition fit

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CAAnalysisSettings":
        d = dict(d)
        if "dilution_mode" not in d and ("dilution_conc" in d or "dilution_signal" in d):
            # older files had two switches that could both be on, which corrects the dilution twice; keep one
            d["dilution_mode"] = ("concentration" if d.get("dilution_conc", True)
                                  else ("signal" if d.get("dilution_signal", False) else "none"))
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class FitResult:
    slope: float
    intercept: float
    slope_se: float
    intercept_se: float
    r2: float
    s_yx: float
    n: int
    x: np.ndarray
    y: np.ndarray
    residuals: np.ndarray
    x_intercept: float            # -intercept / slope
    x_intercept_se: float         # standard-addition uncertainty
    sxx: float = float("nan")     # Σ(x - x̄)²
    t_crit: float = float("nan")  # Student t(0.975, n - 2)
    x_intercept_ci95: float = float("nan")   # t_crit · x_intercept_se
    suitability: float = float("nan")        # |b|·√Sxx / s(y/x): how well the points pin the extrapolation


@dataclass
class CAResult:
    """Output of the full CA standard-addition pipeline."""

    step_times: np.ndarray                 # detected / user step times (len N)
    seg_bounds: list[tuple[float, float]]  # (t0, t1) of every segment, len N+1
    win_bounds: list[tuple[float, float]]  # averaging window of every segment
    plateau: np.ndarray                    # mean current per segment (µA, signed), len N+1
    plateau_sd: np.ndarray
    delta: np.ndarray                      # response relative to baseline, signed, len N+1
    signal: np.ndarray                     # positive-polarity, background / dilution corrected
    conc_added: np.ndarray                 # added concentration per segment (stock unit)
    included: np.ndarray                   # bool mask, len N+1
    polarity: float                        # +1 / -1 applied to |signal|
    fit: Optional[FitResult] = None        # signal vs added concentration (with baseline point)
    sigma: float = 0.0                     # µA
    lod: float = float("nan")
    loq: float = float("nan")
    sensitivity: float = float("nan")      # µA per conc unit
    sensitivity_area: float = float("nan") # µA per conc unit per cm²
    c_x: float = float("nan")              # conc in the measured cell before additions
    c_x_se: float = float("nan")
    c_sample: float = float("nan")         # c_x × V0 / V_sample
    x0: float = float("nan")               # signed x-intercept, -a/b (c_x = |x0|)
    c_x_ci95: float = float("nan")         # half-width of the 95 % interval
    c_sample_se: float = float("nan")
    c_sample_ci95: float = float("nan")
    suitability: float = float("nan")      # |b|·√Sxx / s(y/x)
    sigmas: dict = field(default_factory=dict)    # µA by source: {"fit", "baseline", "blank"}
    lods: dict = field(default_factory=dict)      # by the same keys, in the stock's unit
    loqs: dict = field(default_factory=dict)
    sigma_used: str = ""                   # the source behind ``sigma``
    alt: Optional[dict] = None             # the fit with the baseline point switched the other way
    n_expected: int = 0                    # additions + baseline point (if used)
    volumes_ul: list = field(default_factory=list)   # volumes used per addition (verified ones replace nominal)
    warnings: list[str] = field(default_factory=list)


@dataclass
class CVSettings:
    """Cycle-analysis settings for one CV run (a set of cycles measured together)."""

    scan_rate: float = 0.0                       # V/s; 0 = unknown (charge / capacitance not computed)
    anodic_window: Optional[list[float]] = None  # [E_lo, E_hi] V searched for the anodic peak; None = whole sweep
    cathodic_window: Optional[list[float]] = None
    baseline: str = "none"                       # "none" | "linear" (line across the search window)
    stability_tol: float = 1.0                   # % change between cycles regarded as stable
    excluded_cycles: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CVSettings":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class CVCycleResult:
    cycle: int
    e_pa: float = float("nan")     # V at the anodic peak (max current on the positive-going sweep)
    i_pa: float = float("nan")     # µA (baseline-corrected height if a baseline is used)
    e_pc: float = float("nan")     # V at the cathodic peak (min current on the negative-going sweep)
    i_pc: float = float("nan")
    i_pa_raw: float = float("nan") # measured current at the anodic peak (equals i_pa without a baseline)
    i_pc_raw: float = float("nan")
    e_half: float = float("nan")   # (E_pa + E_pc) / 2
    delta_ep: float = float("nan") # E_pa - E_pc
    ip_ratio: float = float("nan") # |i_pa / i_pc|
    q_anodic: float = float("nan") # µC, ∫ i dt over the positive-going sweep
    q_cathodic: float = float("nan")
    capacitance: float = float("nan")  # µF, (Q_a - Q_c) / (2 ΔE): apparent, includes any Faradaic current
    n_points: int = 0


@dataclass
class ProjectMeta:
    name: str = ""
    operator: str = ""
    electrode: str = ""
    analyte: str = ""
    matrix: str = ""
    notes: str = ""
    created: str = ""
    modified: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProjectMeta":
        known = {f.name for f in fields(cls)}
        return cls(**{k: str(v) for k, v in d.items() if k in known and v is not None})


@dataclass
class Response:
    """One number extracted from a measurement that matters for the sensor (a peak current, Rct, a plateau...).

    Responses are the currency of the sensor study: calibration, reproducibility, stability, selectivity and
    recovery are all computed from a table of them.
    """

    id: str
    source_uid: str = ""
    source_label: str = ""
    technique: str = ""
    analyzer: str = ""
    quantity: str = ""             # e.g. "Peak current", "Rct", "Plateau"
    unit: str = ""
    value: float = float("nan")
    sd: Optional[float] = None
    conc: Optional[float] = None   # concentration (of the condition's analyte) the measurement was made at
    conc_unit: str = "µM"
    condition: str = "analyte"     # which species: "analyte", an interferent's name, ...
    group: str = ""                # electrode / device / batch id
    time: Optional[float] = None   # when it was measured (e.g. days since fabrication), for stability
    role: str = "standard"         # standard | blank | sample
    use: bool = True
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Response":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class ProjectData:
    meta: ProjectMeta = field(default_factory=ProjectMeta)
    files: list[DataFile] = field(default_factory=list)
    ca_settings: dict[str, CAAnalysisSettings] = field(default_factory=dict)
    cv_settings: dict[str, CVSettings] = field(default_factory=dict)
    ui: dict[str, Any] = field(default_factory=dict)
    app_version: str = ""
    responses: list[Response] = field(default_factory=list)          # the sensor study's table
    extras: dict[str, Any] = field(default_factory=dict)             # analysis_params, study settings, electrode ...
