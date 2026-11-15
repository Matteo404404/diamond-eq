"""EQ profiles: creation, storage and export formats."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import numpy as np

from . import curves, optimizer, sources
from .biquad import TYPES, Filter, response_db

VERSION = 1


def data_dir() -> Path:
    d = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "diamondeq" / "profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "profile"


def _curves_block(measurement: np.ndarray | None, filters: list[Filter], preamp: float) -> dict:
    g = curves.grid()
    eq = response_db(filters, g) + preamp
    block = {"freqs": np.round(g, 1).tolist(), "eq": np.round(eq, 2).tolist()}
    if measurement is not None:
        mid = (g >= 200) & (g <= 2000)
        target = curves.diamond_target()
        m = measurement - measurement[mid].mean()
        t = target - target[mid].mean()
        res = m + eq
        block.update(measurement=np.round(m, 2).tolist(), target=np.round(t, 2).tolist(),
                     equalized=np.round(res - res[mid].mean(), 2).tolist())
    return block


def create(name: str, kind: str, measurements: list[dict], options: dict | None = None,
           measurement_db: np.ndarray | None = None) -> dict:
    """Build a profile from one or more squig.link measurements (averaged),
    or from an already loaded response (measurement_db, on curves.grid())."""
    opts = optimizer.FitOptions.from_dict(options)
    if measurement_db is None:
        measurement_db = curves.average([sources.fetch_measurement(m["base"], m["file"]) for m in measurements])
    fit = optimizer.fit(measurement_db, opts)
    label = ", ".join(sorted({m["site"] for m in measurements})) or "file"
    return {
        "version": VERSION,
        "id": slug(f"{name} {label}"),
        "name": name,
        "kind": kind,
        "sources": [{k: m.get(k, "") for k in ("site", "variant", "base", "file")} for m in measurements],
        "options": opts.to_dict(),
        "preamp_db": round(fit.preamp_db, 2),
        "filters": [f.to_dict() for f in fit.filters],
        "stats": fit.stats,
        "curves": _curves_block(measurement_db, fit.filters, fit.preamp_db),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


_APO_FILTER = re.compile(
    r"Filter[^:]*:\s*ON\s+(PK|LSC|HSC|LS|HS|PEQ)\s+Fc\s+([\d.]+)\s*Hz\s+Gain\s+([-+\d.]+)\s*dB(?:\s+Q\s+([\d.]+))?", re.I)


def parse_apo(text: str) -> list[Filter]:
    """Read Equalizer APO / PEQdB / AutoEq parametric EQ text."""
    alias = {"LS": "LSC", "HS": "HSC", "PEQ": "PK"}
    out = []
    for m in _APO_FILTER.finditer(text):
        t = m.group(1).upper()
        t = alias.get(t, t)
        q = float(m.group(4)) if m.group(4) else 0.707
        if t in TYPES and float(m.group(2)) > 0 and q > 0:
            out.append(Filter(t, float(m.group(2)), float(m.group(3)), q))
    if not out:
        raise ValueError("no 'Filter N: ON PK Fc ... Hz Gain ... dB Q ...' lines found")
    return out


def from_apo(name: str, text: str) -> dict:
    """Profile from an existing parametric EQ (e.g. a preset exported from peqdb.com).
    The filters are used as they are; only the preamp is recomputed tightly."""
    filters = parse_apo(text)
    preamp = optimizer.safe_preamp(filters)
    eq = response_db(filters, curves.grid()) + preamp
    return {
        "version": VERSION, "id": slug(f"{name} imported"), "name": name, "kind": "imported", "sources": [],
        "options": {}, "preamp_db": round(preamp, 2), "filters": [f.to_dict() for f in filters],
        "stats": {"preamp_db": round(preamp, 2), "loudness_change_db": round(curves.music_weighted_level(eq), 2)},
        "curves": _curves_block(None, filters, preamp),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def filters_of(profile: dict) -> list[Filter]:
    return [Filter.from_dict(f) for f in profile["filters"]]


def save(profile: dict) -> None:
    (data_dir() / f"{profile['id']}.json").write_text(json.dumps(profile))


def load(profile_id: str) -> dict | None:
    path = data_dir() / f"{slug(profile_id)}.json"
    return json.loads(path.read_text()) if path.exists() else None


def delete(profile_id: str) -> None:
    (data_dir() / f"{slug(profile_id)}.json").unlink(missing_ok=True)


def load_all() -> list[dict]:
    out = []
    for path in sorted(data_dir().glob("*.json")):
        try:
            out.append(json.loads(path.read_text()))
        except ValueError:
            continue
    return out


def summary(profile: dict) -> dict:
    """Profile without the plot curves (for lists and for the phone)."""
    return {k: v for k, v in profile.items() if k != "curves"}


def to_apo(profile: dict) -> str:
    names = {"PK": "PK", "LSC": "LSC", "HSC": "HSC"}
    lines = [f"Preamp: {profile['preamp_db']:.2f} dB"]
    for i, f in enumerate(profile["filters"], 1):
        lines.append(f"Filter {i}: ON {names[f['type']]} Fc {f['fc']:.1f} Hz Gain {f['gain']:.2f} dB Q {f['q']:.3f}")
    return "\n".join(lines) + "\n"


def to_graphic_eq(profile: dict) -> str:
    """127-band GraphicEQ line (Wavelet / Equalizer APO GraphicEQ format)."""
    freqs = np.geomspace(20.0, 20000.0, 127)
    eq = response_db(filters_of(profile), freqs) + profile["preamp_db"]
    return "GraphicEQ: " + "; ".join(f"{f:.0f} {g:.2f}" for f, g in zip(freqs, eq)) + "\n"
