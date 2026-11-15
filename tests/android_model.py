"""Reference model of the Android side, used to check how closely the phone
follows a profile.

`design_bands` mirrors designBands() in android/.../Model.kt and must be kept
in step with it. `dynamics_processing` reproduces the pre-EQ path of Android's
DynamicsProcessing effect (AOSP media/libeffects/dynamicsproc/dsp/DPFrequency.cpp):
blocks with 50% overlap, square-root Hann window before and after the FFT, one
gain per bin, each band covering the bins up to round(cutoff / bin spacing).
"""
import numpy as np

from diamondeq.biquad import response_db

BLOCKS = {"low_latency": 1024, "balanced": 2048, "precise": 4096, "maximum": 8192}


def design_bands(filters, preamp_db, block_at_48k):
    def gain(freq):
        return preamp_db + float(response_db(filters, np.array([freq]))[0])

    bin_hz = 48000.0 / block_at_48k
    step = 2.0 ** (1.0 / 24.0)
    cutoffs, gains = [], []
    k = 0
    while k * bin_hz * (step - 1.0) < bin_hz:
        cutoffs.append((k + 0.49) * bin_hz)
        gains.append(gain(max(k * bin_hz, 10.0)))
        k += 1
    lower = (k - 0.51) * bin_hz
    while lower < 20000.0:
        upper = lower * step
        cutoffs.append(upper)
        gains.append(gain(np.sqrt(lower * upper)))
        lower = upper
    cutoffs.append(200000.0)
    gains.append(gain(20000.0))
    return np.array(cutoffs, np.float32), np.array(gains, np.float32)


def bin_gains(cutoffs, gains_db, block, fs):
    """Linear gain per FFT bin, as DPFrequency::updateParameters computes it."""
    half = block // 2 + 1
    factors = np.ones(half)
    nxt = 0
    for cutoff, g in zip(cutoffs, gains_db):
        stop = int(0.5 + float(cutoff) * block / fs)
        factors[nxt:min(stop, half - 1) + 1] = 10.0 ** (float(g) / 20.0)
        nxt = stop + 1
    return factors


def dynamics_processing(x, cutoffs, gains_db, frame_ms, fs=48000):
    """Run a mono signal through the modelled effect."""
    desired = int(np.float32(frame_ms) * fs / np.float32(1000.0))
    block = desired if desired & (desired - 1) == 0 else 1 << desired.bit_length()
    hop = block // 2
    i = np.arange(block)
    window = np.sqrt(0.5 * (1.0 - np.cos(2 * np.pi * i / (block - 1))))
    factors = bin_gains(cutoffs, gains_db, block, fs)
    out = np.zeros(len(x) + block)
    for start in range(0, len(x) - block + 1, hop):
        spec = np.fft.rfft(x[start:start + block] * window) * factors
        out[start:start + block] += np.fft.irfft(spec, block) * window
    return out[:len(x)], block
