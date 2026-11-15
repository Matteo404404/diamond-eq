"""Measure the EQ that PipeWire actually applies and compare it with the design.

Creates a temporary silent output, attaches the EQ filter to it exactly as for
a real device, plays noise through it and analyses what arrives at the output.
Nothing is played on real devices.
"""
from __future__ import annotations

import subprocess
import tempfile
import time
import warnings
import wave
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from . import pipewire
from .biquad import Filter, response_db

SINK = pipewire.NODE_PREFIX + "selftest"
RECORDER = "diamondeq-selftest-rec"
RATE = 48000
PERIOD, REPEATS = 65536, 9  # periodic noise: 0.73 Hz resolution, ~12 s
SECONDS = PERIOD * REPEATS // RATE + 1


def _noise_file(path: Path) -> None:
    # Flat-spectrum noise that repeats every PERIOD samples. In steady state any
    # PERIOD-long slice of a periodic signal has the same magnitude spectrum, so
    # the two recordings need no time alignment and carry no estimator noise.
    rng = np.random.default_rng(1)
    spec = np.exp(2j * np.pi * rng.random(PERIOD // 2 + 1))
    spec[0] = 0
    x = np.fft.irfft(spec, PERIOD)
    x *= 0.05 / x.std()
    pcm = (np.clip(np.tile(x, REPEATS), -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.repeat(pcm, 2).tobytes())


def _spectrum(path: Path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", wavfile.WavFileWarning)
        rate, data = wavfile.read(path)
    x = data[:, 0].astype(float)
    start = int(np.argmax(np.abs(x) > 1e-4)) + 2 * PERIOD  # skip silence and the filter's settling
    frames = [x[start + i * PERIOD: start + (i + 1) * PERIOD] for i in range(4)]
    if len(frames[-1]) < PERIOD:
        raise RuntimeError("test recording too short")
    p = np.mean([np.abs(np.fft.rfft(fr)) ** 2 for fr in frames], axis=0)
    return np.fft.rfftfreq(PERIOD, 1 / rate), 10 * np.log10(np.maximum(p, 1e-30))


def _pass(noise: Path, out: Path) -> None:
    # The recorder is linked by hand to the output's monitor ports: a normal
    # capture stream aimed at the output would be re-routed to the filter's
    # input by the same policy that routes playback through the filter.
    rec = subprocess.Popen(["pw-cat", "-r", "--target", "0", "-P", "{ node.name = %s }" % RECORDER,
                            "--rate", str(RATE), "--channels", "2", "--format", "f32", str(out)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            time.sleep(0.1)
            linked = [subprocess.run(["pw-link", f"{SINK}:monitor_{ch}", f"{RECORDER}:input_{ch}"],
                                     capture_output=True).returncode == 0 for ch in ("FL", "FR")]
            if all(linked):
                break
        else:
            raise RuntimeError("could not connect the test recorder")
        time.sleep(0.3)
        subprocess.run(["pw-cat", "-p", "--target", SINK, "--volume", "1.0", str(noise)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=SECONDS + 20)
        time.sleep(0.5)
    finally:
        rec.terminate()
        rec.wait(timeout=5)


def run(filters: list[Filter], preamp_db: float) -> dict:
    """Max and RMS deviation (dB) between measured and designed EQ, 20 Hz - 20 kHz."""
    module = subprocess.run(["pactl", "load-module", "module-null-sink", f"sink_name={SINK}",
                             "sink_properties=device.description=DiamondEQ-selftest"],
                            capture_output=True, text=True, check=True).stdout.strip()
    proc = None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            noise, ref, eq = Path(tmp) / "noise.wav", Path(tmp) / "ref.wav", Path(tmp) / "eq.wav"
            _noise_file(noise)
            time.sleep(0.5)
            _pass(noise, ref)
            proc = pipewire.FilterProcess(SINK, "selftest", filters, preamp_db)
            time.sleep(1.5)
            if not proc.alive():
                raise RuntimeError("filter process exited: " + proc.error())
            _pass(noise, eq)
            f, p_ref = _spectrum(ref)
            _, p_eq = _spectrum(eq)
    finally:
        if proc:
            proc.stop()
        subprocess.run(["pactl", "unload-module", module], capture_output=True)
    band = (f >= 20) & (f <= 20000)
    diff = (p_eq - p_ref)[band] - (response_db(filters, f[band], RATE) + preamp_db)
    worst = int(np.argmax(np.abs(diff)))
    return {"max_dev_db": round(float(np.abs(diff).max()), 3), "max_dev_at_hz": round(float(f[band][worst]), 1),
            "rms_dev_db": round(float(np.sqrt(np.mean(diff ** 2))), 4)}
