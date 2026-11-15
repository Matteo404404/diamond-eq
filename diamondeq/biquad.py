"""Parametric EQ filters (RBJ Audio EQ Cookbook, the convention used by
Equalizer APO, PipeWire and PEQdB) and their magnitude responses."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

LOW_SHELF, PEAKING, HIGH_SHELF = "LSC", "PK", "HSC"
TYPES = (LOW_SHELF, PEAKING, HIGH_SHELF)


@dataclass(frozen=True)
class Filter:
    type: str  # LSC | PK | HSC
    fc: float  # Hz
    gain: float  # dB
    q: float

    def to_dict(self) -> dict:
        return {"type": self.type, "fc": round(self.fc, 2), "gain": round(self.gain, 3), "q": round(self.q, 4)}

    @staticmethod
    def from_dict(d: dict) -> "Filter":
        return Filter(str(d["type"]), float(d["fc"]), float(d["gain"]), float(d["q"]))


def coefficients(ftype: str, fc, gain, q, fs: float):
    """Normalised (b0, b1, b2, a1, a2). Arguments may be arrays (broadcast)."""
    fc, gain, q = np.asarray(fc, float), np.asarray(gain, float), np.asarray(q, float)
    a = 10.0 ** (gain / 40.0)
    w0 = 2.0 * np.pi * fc / fs
    cw, alpha = np.cos(w0), np.sin(w0) / (2.0 * q)
    if ftype == PEAKING:
        b0, b1, b2 = 1 + alpha * a, -2 * cw, 1 - alpha * a
        a0, a1, a2 = 1 + alpha / a, -2 * cw, 1 - alpha / a
    elif ftype in (LOW_SHELF, HIGH_SHELF):
        s = 1.0 if ftype == LOW_SHELF else -1.0  # the two shelves mirror each other
        k = 2 * np.sqrt(a) * alpha
        b0 = a * ((a + 1) - s * (a - 1) * cw + k)
        b1 = s * 2 * a * ((a - 1) - s * (a + 1) * cw)
        b2 = a * ((a + 1) - s * (a - 1) * cw - k)
        a0 = (a + 1) + s * (a - 1) * cw + k
        a1 = -s * 2 * ((a - 1) + s * (a + 1) * cw)
        a2 = (a + 1) + s * (a - 1) * cw - k
    else:
        raise ValueError(f"unknown filter type {ftype!r}")
    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


def _magnitude_db(coefs, freqs: np.ndarray, fs: float) -> np.ndarray:
    b0, b1, b2, a1, a2 = (np.asarray(c)[..., None] for c in coefs)
    # Evaluated as a complex ratio: the expanded |H|^2 formula cancels badly
    # for low-frequency filters, whose poles and zeros sit next to z = 1.
    z1 = np.exp(-2j * np.pi * np.asarray(freqs, float) / fs)
    h = (b0 + b1 * z1 + b2 * z1 * z1) / (1.0 + a1 * z1 + a2 * z1 * z1)
    return 20.0 * np.log10(np.maximum(np.abs(h), 1e-15))


def filter_response_db(ftype: str, fc, gain, q, freqs, fs: float) -> np.ndarray:
    """Magnitude in dB; shape (..., len(freqs)) for array parameters."""
    return _magnitude_db(coefficients(ftype, fc, gain, q, fs), freqs, fs)


def response_db(filters, freqs, fs: float = 48000.0) -> np.ndarray:
    """Combined magnitude response of a filter list, in dB."""
    freqs = np.asarray(freqs, float)
    total = np.zeros_like(freqs)
    for f in filters:
        total = total + filter_response_db(f.type, f.fc, f.gain, f.q, freqs, fs)
    return total
