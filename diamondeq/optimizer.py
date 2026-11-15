"""Fit a parametric EQ that moves a measured headphone response onto the target."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import least_squares

from . import curves
from .biquad import HIGH_SHELF, LOW_SHELF, PEAKING, Filter, coefficients, response_db

FC_MIN, FC_MAX = 20.0, 16000.0
Q_BOUNDS = {PEAKING: (0.3, 5.0), LOW_SHELF: (0.4, 1.1), HIGH_SHELF: (0.4, 1.1)}
PREAMP_MARGIN_DB = 0.2
SAMPLE_RATES = (44100.0, 48000.0)  # the preamp must be safe at either rate


@dataclass
class FitOptions:
    bands: int = 10
    # Largest acceptable loudness drop in dB (None = always follow the target
    # exactly). The default only bites on extreme cases, typically a measurement
    # with a deep treble hole that would need a +12 dB boost or more.
    # When the exact correction would cost more, its most expensive boosts are
    # flattened until the loss fits the limit.
    max_loss_db: float | None = 9.0
    max_gain_db: float = 12.0  # per-filter gain limit
    fs: float = 48000.0

    def to_dict(self) -> dict:
        return {"bands": self.bands, "max_loss_db": self.max_loss_db, "max_gain_db": self.max_gain_db}

    @staticmethod
    def from_dict(d: dict | None) -> "FitOptions":
        d = d or {}
        loss = d.get("max_loss_db", 9.0)
        return FitOptions(
            bands=int(min(max(int(d.get("bands", 10)), 1), 20)),
            max_loss_db=None if loss is None else float(min(max(float(loss), 0.0), 30.0)),
            max_gain_db=float(min(max(float(d.get("max_gain_db", 12.0)), 1.0), 20.0)),
        )


@dataclass
class FitResult:
    filters: list[Filter]
    preamp_db: float
    correction: np.ndarray  # wanted EQ shape (dB, mean-centred, after the loss limit)
    exact_correction: np.ndarray  # same before the loss limit
    eq: np.ndarray  # realised EQ incl. preamp (dB)
    stats: dict = field(default_factory=dict)


def fit_weights() -> np.ndarray:
    """Fit weights: full from 40 Hz to 10 kHz, reduced at both extremes.

    The last few Hz above 20 and the top octave matter least to what is heard
    and are where measurements are least repeatable, so filters are not spent
    on them at the expense of the rest.
    """
    g = curves.grid()
    low = np.clip(np.log(g / 15.0) / np.log(40.0 / 15.0), 0.3, 1.0)
    return low * np.where(g <= 10000.0, 1.0, 0.5)


def wanted_correction(measurement_db: np.ndarray, target_db: np.ndarray) -> np.ndarray:
    """EQ shape (dB, arbitrary offset) that takes the measurement to the target.

    Up to 10 kHz this is the smoothed difference of target and measurement. Above 10 kHz
    ear-simulator data stops describing real ears, so the correction fades back
    to the mid-band level by 20 kHz instead of following the data. Checked
    against PEQdB's own presets for two earphones: within 0.2 dB RMS below
    6 kHz and 0.3 dB from 6 to 10 kHz.
    """
    g = curves.grid()
    # the difference is smoothed, not the measurement alone, so a headphone
    # that already sits on the target gets no correction at all
    raw = curves.treble_safe_smoothing(target_db - measurement_db)
    ref = raw[(g >= 200) & (g <= 2000)].mean()
    fade = np.clip(1.0 - np.log(g / 10000.0) / np.log(2.0), 0.0, 1.0)
    return ref + (raw - ref) * fade


def _centre(x: np.ndarray, w: np.ndarray) -> np.ndarray:
    return x - np.sum(x * w) / np.sum(w)


def loudness_loss(eq_shape: np.ndarray) -> float:
    """dB of loudness lost once the shape is shifted down to peak at 0 dB."""
    return -curves.music_weighted_level(eq_shape - eq_shape.max())


def limit_loss(correction: np.ndarray, max_loss_db: float | None) -> np.ndarray:
    """Clip the top of the correction so its loudness cost stays within the limit."""
    if max_loss_db is None or loudness_loss(correction) <= max_loss_db:
        return correction
    lo, hi = correction.min(), correction.max()
    for _ in range(40):  # loss grows monotonically with the ceiling: bisect it
        mid = 0.5 * (lo + hi)
        if loudness_loss(np.minimum(correction, mid)) > max_loss_db:
            hi = mid
        else:
            lo = mid
    return np.minimum(correction, lo)


class _Fit:
    def __init__(self, wanted: np.ndarray, opts: FitOptions):
        self.g = curves.grid()
        self.w = fit_weights()
        self.sw = np.sqrt(self.w)
        self.wanted = wanted
        self.opts = opts
        self.types: list[str] = []
        self.x = np.zeros(0)  # per filter: log2(fc), gain, log2(q)
        self.z1 = np.exp(-2j * np.pi * self.g / opts.fs)
        self.z2 = self.z1 * self.z1

    def _response(self, x: np.ndarray, types: list[str]) -> np.ndarray:
        # Same maths as biquad.response_db, with all filters of a type evaluated
        # together and the unit-circle points precomputed: this runs ~10^5 times.
        total = np.zeros_like(self.g)
        p = x.reshape(-1, 3)
        for t in set(types):
            rows = [i for i, tt in enumerate(types) if tt == t]
            b0, b1, b2, a1, a2 = (c[:, None] for c in coefficients(
                t, 2.0 ** p[rows, 0], p[rows, 1], 2.0 ** p[rows, 2], self.opts.fs))
            h = (b0 + b1 * self.z1 + b2 * self.z2) / (1.0 + a1 * self.z1 + a2 * self.z2)
            total += 20.0 * np.log10(np.maximum(np.abs(h), 1e-15)).sum(axis=0)
        return total

    @staticmethod
    def _q_excess(log_fc: float, log_q: float) -> float:
        # Narrow filters in the treble would chase coupler artefacts: the Q of
        # peaking filters is pushed under 5 (below 3 kHz) falling to 2 (above 8 kHz).
        pos = np.clip((log_fc - np.log2(3000.0)) / np.log2(8000.0 / 3000.0), 0.0, 1.0)
        return max(0.0, log_q - np.log2(5.0 - 3.0 * pos))

    def _residual(self, x: np.ndarray, types: list[str]) -> np.ndarray:
        err = self.sw * _centre(self._response(x, types) - self.wanted, self.w)
        over = [self._q_excess(x[3 * i], x[3 * i + 2]) if t == PEAKING else 0.0 for i, t in enumerate(types)]
        return np.concatenate([err, 20.0 * np.array(over)])

    def _jacobian(self, x: np.ndarray, types: list[str]) -> np.ndarray:
        """Derivative of _residual. The response is a sum over filters, so a
        parameter only changes its own filter: each column costs one single-filter
        evaluation instead of a full one (what generic finite differences do)."""
        n, m = len(types), len(self.g)
        jac = np.zeros((m + n, 3 * n))
        for i, t in enumerate(types):
            p = x[3 * i:3 * i + 3]
            base = self._response(p, [t])
            base_excess = self._q_excess(p[0], p[2]) if t == PEAKING else 0.0
            for k in range(3):
                step = 1e-6 * max(1.0, abs(p[k]))
                q = p.copy()
                q[k] += step
                jac[:m, 3 * i + k] = self.sw * _centre((self._response(q, [t]) - base) / step, self.w)
                if t == PEAKING and k != 1:
                    jac[m + i, 3 * i + k] = 20.0 * (self._q_excess(q[0], q[2]) - base_excess) / step
        return jac

    def _rms_error(self) -> float:
        err = _centre(self._response(self.x, self.types) - self.wanted, self.w)
        return float(np.sqrt(np.sum(self.w * err ** 2) / np.sum(self.w)))

    def _bounds(self, types: list[str]):
        lo, hi = [], []
        for t in types:
            ql, qh = Q_BOUNDS[t]
            lo += [np.log2(FC_MIN), -self.opts.max_gain_db, np.log2(ql)]
            hi += [np.log2(FC_MAX), self.opts.max_gain_db, np.log2(qh)]
        return np.array(lo), np.array(hi)

    def _refine(self, x: np.ndarray, types: list[str], evals: int):
        lo, hi = self._bounds(types)
        x0 = np.clip(x, lo + 1e-6, hi - 1e-6)
        sol = least_squares(self._residual, x0, jac=self._jacobian, bounds=(lo, hi), args=(types,),
                            max_nfev=evals, ftol=1e-7, xtol=1e-7, gtol=1e-7,
                            x_scale=np.tile([0.5, 2.0, 0.5], len(types)))
        return sol.x, float(np.sum(sol.fun ** 2))

    def _candidates(self) -> list[tuple[str, list[float]]]:
        """Starting points for the next filter, placed at the largest remaining error."""
        r = _centre(self.wanted - self._response(self.x, self.types), self.w) * self.w
        r_s = curves.smooth(r, 1 / 6)
        i = int(np.argmax(np.abs(r_s)))
        peak, fc = r_s[i], self.g[i]
        # width of the lobe at half height -> Q of a peaking filter
        above = np.abs(r_s) >= abs(peak) / 2
        left, right = i, i
        while left > 0 and above[left - 1] and np.sign(r_s[left - 1]) == np.sign(peak):
            left -= 1
        while right < len(r_s) - 1 and above[right + 1] and np.sign(r_s[right + 1]) == np.sign(peak):
            right += 1
        bw = max((right - left) / curves.POINTS_PER_OCTAVE, 0.15)
        q = float(np.clip(np.sqrt(2.0 ** bw) / (2.0 ** bw - 1.0), 0.4, 4.0))
        gain = float(np.clip(peak, -self.opts.max_gain_db, self.opts.max_gain_db))
        cands = [(PEAKING, [np.log2(fc), gain, np.log2(q)])]
        # a lobe that runs into the edge of the band is better served by a shelf
        if left == 0 or fc < 150:
            cands.append((LOW_SHELF, [np.log2(self.g[right]), gain, np.log2(0.7)]))
        if right == len(r_s) - 1 or fc > 5000:
            cands.append((HIGH_SHELF, [np.log2(self.g[left]), gain, np.log2(0.7)]))
        return cands

    def _drop_wasted(self) -> None:
        """Remove filters that do nothing, and fold near-identical neighbours together."""
        keep = [i for i in range(len(self.types)) if abs(self.x[3 * i + 1]) >= 0.15]
        merged: list[int] = []
        for i in sorted(keep, key=lambda i: self.x[3 * i]):
            j = merged[-1] if merged else None
            if (j is not None and self.types[i] == self.types[j]
                    and abs(self.x[3 * i] - self.x[3 * j]) < 0.25
                    and abs(self.x[3 * i + 2] - self.x[3 * j + 2]) < 0.6):
                self.x[3 * j + 1] = np.clip(self.x[3 * j + 1] + self.x[3 * i + 1],
                                            -self.opts.max_gain_db, self.opts.max_gain_db)
            else:
                merged.append(i)
        self.types = [self.types[i] for i in merged]
        self.x = np.concatenate([self.x[3 * i:3 * i + 3] for i in merged]) if merged else np.zeros(0)

    def run(self) -> list[Filter]:
        # Add filters one at a time where the remaining error is largest, then
        # polish them together. Polishing can leave filters idle or stacked on
        # each other; those are folded away and the freed slots refilled, at
        # most twice more (an unbounded loop could re-add the same filter forever).
        for _ in range(3):
            while len(self.types) < self.opts.bands and self._rms_error() > 0.03:
                best = None
                for ftype, start in self._candidates():
                    types = self.types + [ftype]
                    x, cost = self._refine(np.concatenate([self.x, start]), types, 30)
                    if best is None or cost < best[2]:
                        best = (types, x, cost)
                self.types, self.x = best[0], best[1]
            if self.types:
                self.x, _ = self._refine(self.x, self.types, 300)
            count = len(self.types)
            self._drop_wasted()
            if len(self.types) == count:
                break
        else:
            if self.types:  # the last fold changed the set: polish what is left
                self.x, _ = self._refine(self.x, self.types, 300)
        out = [Filter(t, float(2.0 ** self.x[3 * i]), float(self.x[3 * i + 1]), float(2.0 ** self.x[3 * i + 2]))
               for i, t in enumerate(self.types)]
        return sorted((f for f in out if abs(f.gain) >= 0.1), key=lambda f: f.fc)


def safe_preamp(filters: list[Filter]) -> float:
    """Gain (dB) that puts the loudest point of the EQ just below 0 dB.

    Checked on a fine grid at 44.1 and 48 kHz, so no frequency is ever amplified
    above digital full scale; positive when the EQ only cuts.
    """
    fine = np.geomspace(10.0, 21000.0, 4000)
    peak = max(float(response_db(filters, fine, fs).max()) for fs in SAMPLE_RATES)
    return -peak - PREAMP_MARGIN_DB


def _band_rms(err: np.ndarray, lo: float, hi: float) -> float:
    g = curves.grid()
    m = (g >= lo) & (g < hi)
    return float(np.sqrt(np.mean(err[m] ** 2)))


def fit(measurement_db: np.ndarray, opts: FitOptions | None = None,
        target_db: np.ndarray | None = None) -> FitResult:
    """measurement_db: raw response on curves.grid() (channels already averaged)."""
    opts = opts or FitOptions()
    target = curves.diamond_target() if target_db is None else target_db
    w = fit_weights()
    exact = _centre(wanted_correction(measurement_db, target), w)
    def attempt(limit):
        shape_wanted = limit_loss(exact, limit)
        filters = _Fit(_centre(shape_wanted, w), opts).run()
        preamp = safe_preamp(filters)
        shape = response_db(filters, curves.grid(), opts.fs)
        return shape_wanted, filters, preamp, shape, -curves.music_weighted_level(shape + preamp)

    limited_shape, filters, preamp, shape, loss = attempt(opts.max_loss_db)
    if opts.max_loss_db is not None and loss > opts.max_loss_db + 0.05:
        # The fitted filters never reproduce the limited shape exactly, so the
        # realised loss lands a little off. Re-aim (secant steps) and keep the
        # most accurate attempt that is inside the requested limit.
        goal, tried = opts.max_loss_db, [(opts.max_loss_db, loss)]
        best = None
        limit = max(goal - (loss - goal), 0.0)
        for _ in range(3):
            cand = attempt(limit)
            tried.append((limit, cand[4]))
            if cand[4] <= goal + 0.05 and (best is None or cand[4] > best[4]):
                best = cand
            if goal - 0.3 <= cand[4] <= goal + 0.05:
                break
            (l0, r0), (l1, r1) = tried[-2], tried[-1]
            step = (goal - r1) * (l1 - l0) / (r1 - r0) if abs(r1 - r0) > 1e-3 else goal - r1
            limit = float(np.clip(l1 + step, 0.0, goal))
        limited_shape, filters, preamp, shape, loss = best or cand
    wanted = _centre(limited_shape, w)
    eq = shape + preamp

    err_exact = _centre(shape - exact, w)
    stats = {
        "preamp_db": round(preamp, 2),
        "loudness_change_db": round(curves.music_weighted_level(eq), 2),
        "exact_loss_db": round(loudness_loss(exact), 2),
        "limited": limited_shape is not exact,
        "error_rms_db": {
            "20-200": round(_band_rms(err_exact, 20, 200), 2),
            "200-2k": round(_band_rms(err_exact, 200, 2000), 2),
            "2k-6k": round(_band_rms(err_exact, 2000, 6000), 2),
            "6k-10k": round(_band_rms(err_exact, 6000, 10000), 2),
            "10k-20k": round(_band_rms(err_exact, 10000, 20001), 2),
        },
        "fit_rms_db": round(float(np.sqrt(np.sum(w * _centre(shape - wanted, w) ** 2) / np.sum(w))), 3),
    }
    return FitResult(filters, preamp, wanted, exact, eq, stats)
