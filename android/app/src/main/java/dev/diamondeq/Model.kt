package dev.diamondeq

import org.json.JSONObject
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt

/** One parametric filter (RBJ cookbook; same convention as the PC side). */
data class Filter(val type: String, val fc: Double, val gain: Double, val q: Double) {

    /** Magnitude in dB at [freq] for sample rate [fs]. */
    fun responseDb(freq: Double, fs: Double = 48000.0): Double {
        val a = 10.0.pow(gain / 40.0)
        val w0 = 2.0 * PI * fc / fs
        val cw = cos(w0)
        val alpha = sin(w0) / (2.0 * q)
        val b0: Double; val b1: Double; val b2: Double
        val a0: Double; val a1: Double; val a2: Double
        if (type == "PK") {
            b0 = 1 + alpha * a; b1 = -2 * cw; b2 = 1 - alpha * a
            a0 = 1 + alpha / a; a1 = -2 * cw; a2 = 1 - alpha / a
        } else {
            val s = if (type == "LSC") 1.0 else -1.0 // the two shelves mirror each other
            val k = 2 * sqrt(a) * alpha
            b0 = a * ((a + 1) - s * (a - 1) * cw + k)
            b1 = s * 2 * a * ((a - 1) - s * (a + 1) * cw)
            b2 = a * ((a + 1) - s * (a - 1) * cw - k)
            a0 = (a + 1) + s * (a - 1) * cw + k
            a1 = -s * 2 * ((a - 1) + s * (a + 1) * cw)
            a2 = (a + 1) + s * (a - 1) * cw - k
        }
        val w = 2.0 * PI * freq / fs
        val c1 = cos(w)
        val c2 = cos(2 * w)
        val num = b0 * b0 + b1 * b1 + b2 * b2 + 2 * (b0 * b1 + b1 * b2) * c1 + 2 * b0 * b2 * c2
        val den = a0 * a0 + a1 * a1 + a2 * a2 + 2 * (a0 * a1 + a1 * a2) * c1 + 2 * a0 * a2 * c2
        return 10.0 * log10(max(num, 1e-30) / max(den, 1e-30))
    }
}

data class Profile(
    val id: String,
    val name: String,
    val source: String,
    val preampDb: Double,
    val filters: List<Filter>,
    /** How much quieter (negative) typical music plays through this profile, preamp included. */
    val loudnessChangeDb: Double = 0.0,
) {
    /** EQ gain in dB at [freq], preamp included. */
    fun gainDb(freq: Double): Double = preampDb + filters.sumOf { it.responseDb(freq) }

    companion object {
        fun fromJson(o: JSONObject): Profile {
            val fs = o.getJSONArray("filters")
            val sources = o.optJSONArray("sources")
            val sites = (0 until (sources?.length() ?: 0))
                .map { sources!!.getJSONObject(it).optString("site") }.filter { it.isNotEmpty() }.distinct()
            return Profile(
                id = o.getString("id"),
                name = o.getString("name"),
                source = sites.joinToString(", "),
                preampDb = o.getDouble("preamp_db"),
                loudnessChangeDb = o.optJSONObject("stats")?.optDouble("loudness_change_db", 0.0) ?: 0.0,
                filters = (0 until fs.length()).map {
                    val f = fs.getJSONObject(it)
                    Filter(f.getString("type"), f.getDouble("fc"), f.getDouble("gain"), f.getDouble("q"))
                },
            )
        }
    }
}

/**
 * Gain in dB (0 or more) to add on top of a profile's preamp so music keeps the
 * loudness it has without EQ.
 *
 * The preamp is sized so that a full-scale signal cannot clip. When the volume
 * is applied digitally and is turned down by [volumeAttenuationDb], that much
 * headroom is unused, and the make-up gain may use it (less 0.5 dB of margin)
 * without any risk of clipping. At full volume the result is 0.
 */
fun makeUpGainDb(profile: Profile, volumeAttenuationDb: Double): Double =
    max(0.0, minOf(-profile.loudnessChangeDb, volumeAttenuationDb - 0.5))

/**
 * How finely Android's equalizer resolves the bass. The engine works on blocks
 * of [blockAt48k] samples: longer blocks follow the curve more closely at low
 * frequencies but delay the sound by about one block.
 */
enum class Precision(val label: String, val blockAt48k: Int) {
    LOW_LATENCY("Low delay (21 ms): for games", 1024),
    BALANCED("Balanced (43 ms): for video", 2048),
    PRECISE("Precise (85 ms): for music", 4096),
    MAXIMUM("Maximum (171 ms): music only", 8192);

    /** Frame duration to request: just under the block length, which the engine rounds up to it. */
    val frameMs: Float get() = blockAt48k / 48f - 0.5f
    val binHz: Double get() = 48000.0 / blockAt48k
}

/** Equalizer bands for Android's DynamicsProcessing: upper band edges (Hz) and gains (dB). */
class Bands(val cutoffs: FloatArray, val gains: FloatArray)

/**
 * Lays the EQ curve onto DynamicsProcessing bands.
 *
 * The engine applies one gain per FFT bin: a band covers every bin up to
 * round(cutoff / binHz), with no interpolation. So each low bin gets its own
 * band, set to the curve's value at that bin's exact frequency; from the point
 * where bins are closer together than 1/24 octave, bands are 1/24 octave wide.
 * The bin spacing is the same at 48, 96 and 192 kHz and 8% finer at 44.1 kHz.
 */
fun designBands(profile: Profile, precision: Precision): Bands {
    val binHz = precision.binHz
    val step = 2.0.pow(1.0 / 24.0)
    val cutoffs = ArrayList<Float>()
    val gains = ArrayList<Float>()
    var k = 0
    while (k * binHz * (step - 1.0) < binHz) { // one band per bin
        cutoffs.add(((k + 0.49) * binHz).toFloat())
        gains.add(profile.gainDb(max(k * binHz, 10.0)).toFloat())
        k++
    }
    var lower = (k - 0.51) * binHz
    while (lower < 20000.0) {
        val upper = lower * step
        cutoffs.add(upper.toFloat())
        gains.add(profile.gainDb(sqrt(lower * upper)).toFloat())
        lower = upper
    }
    // everything above 20 kHz, up to any Nyquist frequency
    cutoffs.add(200000f)
    gains.add(profile.gainDb(20000.0).toFloat())
    return Bands(cutoffs.toFloatArray(), gains.toFloatArray())
}
