"""Electrochemical techniques the platform knows about, and how a measurement is recognised (no Qt).

Recognition uses, in this order: the potentiostat's method id, then words in the curve title.  PalmSens method ids
were not all verifiable against real files, so the user can always override a measurement's technique in the app.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Technique:
    key: str
    label: str
    family: str                  # voltammetry | amperometry | impedance | potentiometry | other
    x_label: str
    y_label: str
    method_ids: tuple[str, ...] = ()
    words: str = ""              # regex searched (case-insensitive) in the curve / run title


CA, CV, LSV, DPV, SWV, NPV, ACV = "CA", "CV", "LSV", "DPV", "SWV", "NPV", "ACV"
CC, CP, OCP, EIS, OTHER = "CC", "CP", "OCP", "EIS", "OTHER"

TECHNIQUES: dict[str, Technique] = {t.key: t for t in (
    Technique(CA, "Chronoamperometry / amperometry (i–t)", "amperometry", "Time (s)", "Current (µA)",
              ("ad", "ca", "fam", "ma", "pad", "mpad"), r"\bca\b|chronoamperom|amperometr|\bi[- ]?t\b|\bi vs t\b"),
    Technique(CV, "Cyclic voltammetry", "voltammetry", "Potential (V)", "Current (µA)", ("cv",), r"\bcv\b|cyclic"),
    Technique(LSV, "Linear sweep voltammetry", "voltammetry", "Potential (V)", "Current (µA)", ("lsv",),
              r"\blsv\b|linear sweep"),
    Technique(DPV, "Differential pulse voltammetry", "voltammetry", "Potential (V)", "Current (µA)", ("dpv",),
              r"\bdpv\b|differential pulse"),
    Technique(SWV, "Square wave voltammetry", "voltammetry", "Potential (V)", "Current (µA)", ("swv",),
              r"\bswv\b|square[- ]?wave"),
    Technique(NPV, "Normal pulse voltammetry", "voltammetry", "Potential (V)", "Current (µA)", ("npv",),
              r"\bnpv\b|normal pulse"),
    Technique(ACV, "AC voltammetry", "voltammetry", "Potential (V)", "Current (µA)", ("acv",), r"\bacv\b|ac voltammetry"),
    Technique(CC, "Chronocoulometry", "amperometry", "Time (s)", "Charge (µC)", ("cc",), r"\bcc\b|chronocoulom"),
    Technique(CP, "Chronopotentiometry", "potentiometry", "Time (s)", "Potential (V)", ("cp",),
              r"chronopotentiom|\bcp\b"),
    Technique(OCP, "Open-circuit potential / potentiometry", "potentiometry", "Time (s)", "Potential (V)", ("ocp", "pot"),
              r"open[- ]?circuit|\bocp\b|(?<!chrono)potentiometr"),
    Technique(EIS, "Electrochemical impedance spectroscopy", "impedance", "Z′ (Ω)", "−Z″ (Ω)",
              ("eis", "ei", "gis", "fis"), r"impedance|\beis\b|nyquist|bode|z'|z′"),
    Technique(OTHER, "Other / generic x–y", "other", "x", "y"),
)}

ORDER = [CA, CV, LSV, DPV, SWV, NPV, ACV, CC, CP, OCP, EIS, OTHER]
_BY_METHOD = {m: t.key for t in TECHNIQUES.values() for m in t.method_ids}
_PRIORITY = [EIS, CP, OCP, CC, DPV, SWV, NPV, ACV, LSV, CV, CA]          # most specific words first


def classify(title: str = "", method_id: str = "") -> str:
    """Technique key for a curve / run title and an (optional) instrument method id."""
    mid = (method_id or "").strip().lower()
    if mid in _BY_METHOD:
        return _BY_METHOD[mid]
    text = (title or "").lower()
    for key in _PRIORITY:
        w = TECHNIQUES[key].words
        if w and re.search(w, text):
            return key
    return OTHER


def label(key: str) -> str:
    return TECHNIQUES.get(key, TECHNIQUES[OTHER]).label


def short(key: str) -> str:
    return key if key != OTHER else "xy"


def family(key: str) -> str:
    return TECHNIQUES.get(key, TECHNIQUES[OTHER]).family


def is_voltammetric(key: str) -> bool:
    return family(key) == "voltammetry"
