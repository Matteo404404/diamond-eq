"""How closely the phone's equalizer (Android DynamicsProcessing, driven by the
app's band layout) reproduces a profile."""
import numpy as np
import pytest

from android_model import BLOCKS, design_bands, dynamics_processing
from diamondeq.biquad import Filter, response_db

# The INAWAKEN Dawn Ms profile (Audio Amigo measurement, 10 filters).
FILTERS = [
    Filter("PK", 47.0, 0.50, 1.04), Filter("PK", 61.7, 0.58, 1.44), Filter("PK", 175.7, -1.20, 1.14),
    Filter("PK", 759.8, 2.97, 1.00), Filter("HSC", 1292.0, -1.35, 0.46), Filter("HSC", 1976.9, 2.90, 0.98),
    Filter("PK", 3003.8, 0.40, 1.95), Filter("PK", 4517.3, -1.77, 1.90), Filter("PK", 5776.8, 1.67, 3.00),
    Filter("PK", 9568.6, 7.77, 0.99),
]
PREAMP = -9.59
# A demanding case: the Diamond bass shelf itself plus a narrow bass peak.
BASSY = [Filter("LSC", 107.0, 11.8, 0.6), Filter("PK", 60.0, 4.0, 3.0), Filter("HSC", 4448.0, 2.7, 0.7)]

PERIOD = 65536


def measured_error(filters, preamp, precision, fs=48000):
    """Max |error| in dB per band between the modelled phone output and the profile."""
    block = BLOCKS[precision]
    cutoffs, gains = design_bands(filters, preamp, block)
    rng = np.random.default_rng(0)
    spec = np.exp(2j * np.pi * rng.random(PERIOD // 2 + 1))
    spec[0] = 0
    one = np.fft.irfft(spec, PERIOD)
    y, used_block = dynamics_processing(np.tile(one, 5), cutoffs, gains, block / 48.0 - 0.5, fs)
    assert used_block == block * (2 if fs > 50000 else 1)
    seg = y[2 * PERIOD:3 * PERIOD]  # steady state; the output repeats with the input
    f = np.fft.rfftfreq(PERIOD, 1 / fs)
    got = 20 * np.log10(np.abs(np.fft.rfft(seg)) / np.abs(np.fft.rfft(one)).clip(1e-12))
    err = got - (response_db(filters, f, 48000.0) + preamp)
    # the audible error: averaged over 1/6 octave, like any tonal-balance measure
    logf = np.log2(f.clip(1e-3))
    centres = np.geomspace(20, 20000, 180)
    smooth = np.array([err[np.abs(logf - np.log2(c)) <= 1 / 12].mean() for c in centres])
    return {name: float(np.abs(smooth[(centres >= lo) & (centres < hi)]).max())
            for name, (lo, hi) in {"20-50": (20, 50), "50-200": (50, 200), "200-20k": (200, 20001)}.items()}


@pytest.mark.parametrize("filters,preamp,precision,limits", [
    # a typical profile: broad filters only
    (FILTERS, PREAMP, "precise", {"20-50": 0.1, "50-200": 0.1, "200-20k": 0.1}),
    (FILTERS, PREAMP, "maximum", {"20-50": 0.05, "50-200": 0.05, "200-20k": 0.1}),
    (FILTERS, PREAMP, "balanced", {"20-50": 0.2, "50-200": 0.25, "200-20k": 0.1}),
    # a narrow bass peak needs the long blocks; shorter ones blur it
    (BASSY, -12.0, "precise", {"20-50": 0.25, "50-200": 0.45, "200-20k": 0.1}),
    (BASSY, -12.0, "maximum", {"20-50": 0.15, "50-200": 0.3, "200-20k": 0.15}),
    (BASSY, -12.0, "balanced", {"200-20k": 0.1}),
])
def test_phone_follows_profile(filters, preamp, precision, limits):
    err = measured_error(filters, preamp, precision)
    for band, limit in limits.items():
        assert err[band] <= limit, (precision, band, err)


def test_same_accuracy_at_96k():
    err = measured_error(FILTERS, PREAMP, "precise", fs=96000)
    assert err["50-200"] <= 0.15 and err["200-20k"] <= 0.1, err


if __name__ == "__main__":
    for name, (fl, pre) in {"Dawn Ms": (FILTERS, PREAMP), "bass shelf + narrow peak": (BASSY, -12.0)}.items():
        for prec in BLOCKS:
            print(f"{name:26} {prec:12}", {k: round(v, 2) for k, v in measured_error(fl, pre, prec).items()})
