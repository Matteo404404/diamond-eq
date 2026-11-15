"""Frequency grid, the Diamond target, and measurement parsing/conditioning."""
from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources

import numpy as np

F_MIN, F_MAX = 20.0, 20000.0
POINTS_PER_OCTAVE = 48


@lru_cache(maxsize=1)
def grid() -> np.ndarray:
    """Log-spaced analysis grid, 20 Hz - 20 kHz."""
    n = int(round(np.log2(F_MAX / F_MIN) * POINTS_PER_OCTAVE)) + 1
    g = np.geomspace(F_MIN, F_MAX, n)
    g.setflags(write=False)
    return g


def interp_log(freqs, xf, xdb) -> np.ndarray:
    """Interpolate on log-frequency; holds the end values outside the data."""
    return np.interp(np.log(np.asarray(freqs, float)), np.log(np.asarray(xf, float)), np.asarray(xdb, float))


@lru_cache(maxsize=1)
def _target_data():
    text = resources.files("diamondeq").joinpath("data/diamond_target.csv").read_text()
    rows = [ln.split(",") for ln in text.splitlines() if ln and ln[0].isdigit()]
    data = np.array(rows, float)
    return data[:, 0], data[:, 1]


def diamond_target(freqs=None) -> np.ndarray:
    """PEQdB Diamond target (dB) on an IEC 60318-4 ear simulator."""
    f, db = _target_data()
    return interp_log(grid() if freqs is None else freqs, f, db)


_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def parse_measurement(text: str) -> tuple[np.ndarray, np.ndarray]:
    """Parse a frequency-response text file (REW / squig.link / CSV).

    Uses the first two numeric columns of every data line; comment and header
    lines are skipped. Returns frequency-sorted (Hz, dB) arrays.
    """
    pts = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in "*#;":
            continue
        nums = _NUM.findall(line.replace(",", " ").replace(";", " "))
        if len(nums) < 2 or not (line[0].isdigit() or line[0] in "+-."):
            continue
        f, db = float(nums[0]), float(nums[1])
        if 1.0 <= f <= 96000.0 and -200.0 < db < 200.0:
            pts.append((f, db))
    if len(pts) < 20:
        raise ValueError("not a frequency-response measurement (fewer than 20 data points)")
    arr = np.array(sorted(pts))
    f, idx = np.unique(arr[:, 0], return_index=True)
    return f, arr[idx, 1]


def to_grid(f, db) -> np.ndarray:
    return interp_log(grid(), f, db)


def average(curves: list[np.ndarray]) -> np.ndarray:
    """Average channels/samples after aligning their mid-band levels."""
    g = grid()
    band = (g >= 200) & (g <= 2000)
    aligned = [c - c[band].mean() for c in curves]
    return np.mean(aligned, axis=0)


def smooth(db: np.ndarray, width_oct) -> np.ndarray:
    """Variable fractional-octave smoothing on the analysis grid.

    width_oct is a scalar or per-point array: the full window width in octaves.
    """
    n = len(db)
    half = np.maximum(np.broadcast_to(np.asarray(width_oct, float), (n,)) * POINTS_PER_OCTAVE / 2.0, 0.0)
    csum = np.concatenate([[0.0], np.cumsum(db)])
    out = np.empty(n)
    for i in range(n):
        h = int(round(half[i]))
        lo, hi = max(0, i - h), min(n, i + h + 1)
        out[i] = (csum[hi] - csum[lo]) / (hi - lo)
    return out


def treble_safe_smoothing(db: np.ndarray, lo_width: float = 1 / 12, hi_width: float = 2 / 3,
                          f_start: float = 4000.0, f_end: float = 7000.0) -> np.ndarray:
    """Light smoothing in bass/mids, wide smoothing in the upper treble.

    Coupler measurements have narrow treble peaks and dips whose positions change
    with insertion depth and ear shape. Correcting them exactly makes real ears
    sound worse, so above ~f_start only the broad trend is kept. The defaults
    reproduce the correction PEQdB's own presets apply (within 0.4 dB RMS).
    """
    g = grid()
    t = np.clip(np.log(g / f_start) / np.log(f_end / f_start), 0.0, 1.0)
    width = lo_width + (hi_width - lo_width) * t
    return smooth(db, width)


def music_weighted_level(eq_db: np.ndarray) -> float:
    """Estimated loudness change (dB) of typical music passed through eq_db.

    Music is modelled with the usual mastered-pop long-term spectrum: -4.5 dB per
    octave per Hz, which is -1.5 dB per octave on this log-spaced grid, rolling
    off below 50 Hz. Loudness is weighted like ITU-R BS.1770 (K-weighting).
    """
    g = grid()
    music = -1.5 * np.log2(g / 1000.0) - 10.0 * np.log10(1.0 + (50.0 / g) ** 4)
    k_shelf = 4.0 / (1.0 + (1681.0 / g) ** 2)
    k_highpass = -20.0 * np.log10(1.0 + (38.0 / g) ** 2)
    w = 10.0 ** ((music + k_shelf + k_highpass) / 10.0)
    return float(10.0 * np.log10(np.sum(w * 10.0 ** (eq_db / 10.0)) / np.sum(w)))
