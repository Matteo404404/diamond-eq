import json

import numpy as np
import pytest
from scipy.signal import freqz

from diamondeq import curves, optimizer, pipewire, profile, sources
from diamondeq.biquad import Filter, coefficients, response_db

DIAMOND = [Filter("LSC", 107.0, 11.8, 0.6), Filter("PK", 2724.0, -3.6, 1.2), Filter("HSC", 4448.0, 2.7, 0.7)]


@pytest.mark.parametrize("f", DIAMOND + [Filter("PK", 60.0, 6.0, 4.0), Filter("HSC", 12000.0, -5.0, 1.1)])
def test_response_matches_scipy(f):
    b0, b1, b2, a1, a2 = coefficients(f.type, f.fc, f.gain, f.q, 48000.0)
    freqs = np.geomspace(20, 20000, 300)
    _, h = freqz([b0, b1, b2], [1.0, a1, a2], worN=freqs, fs=48000.0)
    assert np.allclose(response_db([f], freqs), 20 * np.log10(np.abs(h)), atol=1e-9)


def test_filter_shapes():
    assert response_db([Filter("LSC", 100, 6, 0.7)], [10.0])[0] == pytest.approx(6.0, abs=0.05)
    assert response_db([Filter("HSC", 4000, -4, 0.7)], [20000.0])[0] == pytest.approx(-4.0, abs=0.05)
    assert response_db([Filter("PK", 1000, 5, 2)], [1000.0])[0] == pytest.approx(5.0, abs=1e-6)


def test_target_is_diffuse_field_plus_diamond_filters():
    """The stored curve minus the three published Diamond filters must be the
    diffuse-field baseline: flat in the bass, with the ear-gain peak near 3 kHz."""
    g = curves.grid()
    df = curves.diamond_target() - response_db(DIAMOND, g)
    bass = df[(g >= 20) & (g <= 200)]
    assert bass.max() - bass.min() < 1.0
    assert 2500 < g[np.argmax(df)] < 3500
    assert 14 < df.max() - bass.mean() < 18


def test_parse_measurement_formats():
    rew = "* Measurement data\n* Freq(Hz) SPL(dB) Phase(degrees)\n" + "\n".join(
        f"{f:.3f} {60 + i * 0.01:.3f} 0.0" for i, f in enumerate(np.geomspace(20, 20000, 100)))
    f, db = curves.parse_measurement(rew)
    assert len(f) == 100 and f[0] == pytest.approx(20.0) and db[-1] == pytest.approx(60.99)
    csv = "frequency,raw\n" + "\n".join(f"{f:.2f},{1.5:.2f}" for f in np.geomspace(20, 20000, 50))
    f, db = curves.parse_measurement(csv)
    assert len(f) == 50 and np.all(db == 1.5)
    with pytest.raises(ValueError):
        curves.parse_measurement("<html>403 Forbidden</html>")


def _headphone(deviation):
    """A headphone whose response is the target plus `deviation` (dB)."""
    return curves.diamond_target() + deviation


def test_fit_recovers_known_deviation():
    g = curves.grid()
    wrong = response_db([Filter("PK", 200, 4.0, 1.0), Filter("PK", 3000, -5.0, 2.0), Filter("LSC", 80, -3.0, 0.7)], g)
    fit = optimizer.fit(_headphone(wrong), optimizer.FitOptions(bands=8, max_loss_db=None))
    mid = (g >= 30) & (g <= 9000)
    residual = (fit.eq - fit.preamp_db) + wrong
    residual -= residual[mid].mean()
    assert np.abs(residual[mid]).max() < 0.5
    assert fit.stats["error_rms_db"]["200-2k"] < 0.2


def test_preamp_never_amplifies_and_is_tight():
    g = curves.grid()
    wrong = response_db([Filter("PK", 5000, -8.0, 1.0), Filter("LSC", 100, -6.0, 0.7)], g)  # needs boosts
    fit = optimizer.fit(_headphone(wrong), optimizer.FitOptions(max_loss_db=None))
    fine = np.geomspace(10, 21000, 5000)
    for fs in (44100.0, 48000.0):
        peak = response_db(fit.filters, fine, fs).max() + fit.preamp_db
        assert -0.5 < peak <= 0.0


def test_loss_limit_is_respected():
    g = curves.grid()
    wrong = response_db([Filter("HSC", 4000, -14.0, 0.7)], g)  # dull treble: needs a +14 dB boost
    exact = optimizer.fit(_headphone(wrong), optimizer.FitOptions(max_loss_db=None))
    limited = optimizer.fit(_headphone(wrong), optimizer.FitOptions(max_loss_db=6.0))
    assert exact.stats["loudness_change_db"] < -9.0
    assert -6.1 <= limited.stats["loudness_change_db"] <= -5.0
    assert limited.stats["limited"] and not exact.stats["limited"]


def test_apo_round_trip():
    prof = profile.from_apo("x", "Preamp: -6 dB\nFilter 1: ON PK Fc 105 Hz Gain 3.0 dB Q 0.70\n"
                                 "Filter 2: ON LSC Fc 80 Hz Gain -2.5 dB Q 0.71\nFilter 3: OFF PK Fc 1 Hz Gain 9 dB Q 1\n"
                                 "Filter 4: ON HS Fc 8000 Hz Gain 1.0 dB\n")
    assert [f["type"] for f in prof["filters"]] == ["PK", "LSC", "HSC"]
    again = profile.parse_apo(profile.to_apo(prof))
    assert [f.to_dict() for f in again] == prof["filters"]
    # the preamp is recomputed from the real peak of the curve, not copied from the file
    peak = response_db(again, np.geomspace(10, 21000, 4000)).max() + prof["preamp_db"]
    assert -0.3 < peak <= 0.0 and prof["preamp_db"] > -6.0
    assert profile.to_graphic_eq(prof).startswith("GraphicEQ: 20 ")
    with pytest.raises(ValueError):
        profile.parse_apo("nothing here")


def test_pipewire_config():
    text = pipewire.config_text("alsa_output.test", 'My "IEM"', DIAMOND, -6.0206)
    assert 'filter.smart.target = { node.name = "alsa_output.test" }' in text
    assert '"Mult" = 0.5000' in text
    assert text.count("label = bq_") == 3 and "bq_lowshelf" in text and "bq_highshelf" in text
    assert 'output = "band2:Out" input = "band3:In"' in text and 'outputs = [ "band3:Out" ]' in text
    assert '\\"IEM\\"' in text  # the title is quoted safely
    assert pipewire.config_text("s", "t", [], 0.0).count("links = [") == 1


def test_search_ranking(monkeypatch):
    def entry(name, user):
        return {"name": name, "key": sources._norm(name), "kind": "IEMs", "site": user, "user": user,
                "base": "https://x", "files": [name], "variants": [""]}
    index = {"entries": [entry("INAWAKEN Dawn-Ms", "smallsite"), entry("InAwaken Dawn Ms", "audioamigo"),
                         entry("Fearless Dawn", "superreview")]}
    monkeypatch.setattr(sources, "load_index", lambda *a, **k: index)
    hits = sources.search("dawn ms")
    assert len(hits) == 1 and [m["user"] for m in hits[0]["measurements"]] == ["audioamigo", "smallsite"]
    assert sources.search("zzz") == [] and sources.search("  ") == []


def test_profile_json_is_plain(monkeypatch):
    prof = profile.create("Test", "IEMs", [], measurement_db=_headphone(np.zeros(len(curves.grid()))))
    json.dumps(prof)
    assert prof["filters"] == [] or max(abs(f["gain"]) for f in prof["filters"]) < 0.5
    assert abs(prof["preamp_db"]) < 0.8


def test_boost_raises_preamp_but_never_above_original_loudness():
    g = curves.grid()
    dull = _headphone(response_db([Filter("HSC", 4000, -10.0, 0.7)], g))  # needs a +10 dB treble boost
    plain = optimizer.fit(dull, optimizer.FitOptions(max_loss_db=None))
    boosted = optimizer.fit(dull, optimizer.FitOptions(max_loss_db=None, boost_db=4.0))
    assert boosted.preamp_db == pytest.approx(plain.preamp_db + 4.0, abs=0.05)
    assert boosted.stats["safe_preamp_db"] == pytest.approx(plain.preamp_db, abs=0.05)
    assert [f.to_dict() for f in boosted.filters] == [f.to_dict() for f in plain.filters]
    # a cut-only EQ loses almost no level, so there is next to nothing to give back
    cut_only = [Filter("PK", 3000, -4.0, 2.0)]
    preamp, used = optimizer.apply_boost(cut_only, 6.0)
    assert used < 1.0 and curves.music_weighted_level(response_db(cut_only, g) + preamp) <= 0.01
    imported = profile.from_apo("x", "Filter 1: ON HSC Fc 4000 Hz Gain 10 dB Q 0.7", {"boost_db": 6})
    assert imported["stats"]["boost_db"] == 6.0 and imported["options"]["boost_db"] == 6.0
