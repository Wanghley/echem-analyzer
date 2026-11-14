"""The devices a study is about: which electrode belongs to which design variant and lot, and how it was made (no Qt).

A response already carries the name of the electrode it came from (``Response.group``).  A :class:`Device` adds what the lab knows about
that electrode: its **variant** (a design or recipe), its **lot** (a fabrication batch), the date, free-form **fabrication parameters**
(ink volume, deposition time, annealing temperature, ...) and notes.  With that, results can be compared across variants and plotted
against any fabrication parameter, and the electrodes of a batch can be screened against acceptance criteria.

Devices are stored in the project under ``extras["devices"]`` as a list of dictionaries, so a project without them is unchanged.
"""
from __future__ import annotations

import copy
import csv
import io
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from .models import Response

KEY = "devices"
FIXED_COLUMNS = ("id", "variant", "lot", "fabricated", "notes")


@dataclass
class Device:
    id: str                                              # the electrode name used as ``Response.group``
    variant: str = ""
    lot: str = ""
    fabricated: str = ""                                 # a date, kept as the text the user typed (2026-11-05, "5 Nov", ...)
    notes: str = ""
    params: dict[str, Any] = field(default_factory=dict)  # fabrication parameters: numbers where the text is a number, else text

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "variant": self.variant, "lot": self.lot, "fabricated": self.fabricated, "notes": self.notes,
                "params": dict(self.params)}

    @classmethod
    def from_dict(cls, d: Any) -> Optional["Device"]:
        if not isinstance(d, dict) or not str(d.get("id", "")).strip():
            return None
        params = d.get("params") if isinstance(d.get("params"), dict) else {}
        return cls(id=str(d["id"]).strip(), variant=str(d.get("variant", "") or ""), lot=str(d.get("lot", "") or ""),
                   fabricated=str(d.get("fabricated", "") or ""), notes=str(d.get("notes", "") or ""),
                   params={str(k): v for k, v in params.items() if str(k).strip()})


def parse_value(text: Any) -> Any:
    """A fabrication parameter as typed: a number when it is one ("2.5", "2,5", "1e-3"), else the text; blank stays blank."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text) if math.isfinite(text) else ""
    t = str(text).strip()
    if not t:
        return ""
    try:
        v = float(t.replace(",", "."))
        return v if math.isfinite(v) else t
    except ValueError:
        return t


def is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def load(extras: dict) -> dict[str, Device]:
    """The devices of a project, by id.  Anything malformed in a hand-edited project is skipped, not fatal."""
    out: dict[str, Device] = {}
    for d in extras.get(KEY) or []:
        dev = Device.from_dict(d)
        if dev is not None:
            out.setdefault(dev.id, dev)
    return out


def save(extras: dict, devices: dict[str, Device]) -> None:
    extras[KEY] = [d.to_dict() for d in devices.values()]


def sync(devices: dict[str, Device], responses: Iterable[Response]) -> dict[str, Device]:
    """The devices plus an empty one for every electrode name found in the responses; none is ever removed (it may hold notes)."""
    out = dict(devices)
    for r in responses:
        if r.group and r.group not in out:
            out[r.group] = Device(id=r.group)
    return out


def variants(devices: dict[str, Device]) -> list[str]:
    return sorted({d.variant for d in devices.values() if d.variant}, key=str.lower)


def lots(devices: dict[str, Device]) -> list[str]:
    return sorted({d.lot for d in devices.values() if d.lot}, key=str.lower)


def param_names(devices: dict[str, Device]) -> list[str]:
    """Every fabrication parameter named on any device, in the order first met."""
    seen: dict[str, None] = {}
    for d in devices.values():
        for k in d.params:
            seen.setdefault(k, None)
    return list(seen)


def numeric_params(devices: dict[str, Device]) -> list[str]:
    """The parameters that are numbers on at least two devices with different values (the ones a trend can be drawn against)."""
    out = []
    for name in param_names(devices):
        values = [d.params[name] for d in devices.values() if is_number(d.params.get(name))]
        if len(values) >= 2 and len(set(values)) >= 2:
            out.append(name)
    return out


def of_variant(devices: dict[str, Device], variant: str) -> list[Device]:
    return [d for d in devices.values() if d.variant == variant]


# --------------------------------------------------------------------------- CSV for a lab notebook

def to_csv(devices: dict[str, Device]) -> str:
    """One row per device: id, variant, lot, fabricated, notes, then a column per fabrication parameter."""
    names = param_names(devices)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([*FIXED_COLUMNS, *names])
    for d in devices.values():
        w.writerow([d.id, d.variant, d.lot, d.fabricated, d.notes,
                    *[("" if d.params.get(n, "") == "" else (f"{d.params[n]:g}" if is_number(d.params[n]) else d.params[n])) for n in names]])
    return buf.getvalue()


def from_csv(text: str) -> list[Device]:
    """Devices from CSV text (comma, semicolon or tab separated); columns other than the fixed ones become fabrication parameters.

    The header names the columns, in any order and case; a row without an id is skipped, and a repeated id keeps its first row.
    """
    text = text.lstrip("\ufeff")
    if not text.strip():
        return []
    head = text.splitlines()[0]
    delim = max((",", ";", "\t"), key=head.count)
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    names = [c.strip() for c in rows[0]]
    lower = [c.lower() for c in names]
    if "id" not in lower:
        raise ValueError("The first row must name the columns, including one called id (the electrode name).")
    out: dict[str, Device] = {}
    for row in rows[1:]:
        cells = {names[i]: (row[i].strip() if i < len(row) else "") for i in range(len(names))}
        get = lambda key: next((cells[names[i]] for i, c in enumerate(lower) if c == key), "")                 # noqa: E731
        dev_id = get("id")
        if not dev_id or dev_id in out:
            continue
        params = {names[i]: parse_value(cells[names[i]]) for i, c in enumerate(lower)
                  if c not in FIXED_COLUMNS and names[i] and cells[names[i]] != ""}
        out[dev_id] = Device(id=dev_id, variant=get("variant"), lot=get("lot"), fabricated=get("fabricated"), notes=get("notes"), params=params)
    return list(out.values())


def merge(devices: dict[str, Device], incoming: Sequence[Device]) -> dict[str, Device]:
    """A new registry with the imported devices added; one already there takes the values the import fills in.  The input is not changed."""
    out = {k: copy.deepcopy(v) for k, v in devices.items()}
    for new in incoming:
        old = out.get(new.id)
        if old is None:
            out[new.id] = new
            continue
        for attr in ("variant", "lot", "fabricated", "notes"):
            if getattr(new, attr):
                setattr(old, attr, getattr(new, attr))
        old.params.update(new.params)
    return out
