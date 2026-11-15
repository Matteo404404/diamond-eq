package dev.diamondeq

import android.content.Context
import org.json.JSONObject
import java.io.File

/** Everything the app remembers: switches, synced profiles and per-output choices. */
class Store(context: Context) {
    private val prefs = context.getSharedPreferences("diamondeq", Context.MODE_PRIVATE)
    private val syncFile = File(context.filesDir, "sync.json")

    var enabled: Boolean
        get() = prefs.getBoolean("enabled", true)
        set(v) = prefs.edit().putBoolean("enabled", v).apply()

    var precision: Precision
        get() = Precision.entries.getOrElse(prefs.getInt("precision", Precision.PRECISE.ordinal)) { Precision.PRECISE }
        set(v) = prefs.edit().putInt("precision", v.ordinal).apply()

    /** Make up for the EQ's level drop from unused volume headroom (wired and USB outputs). */
    var keepLoudness: Boolean
        get() = prefs.getBoolean("keepLoudness", true)
        set(v) = prefs.edit().putBoolean("keepLoudness", v).apply()

    var lastSync: Long
        get() = prefs.getLong("lastSync", 0L)
        private set(v) = prefs.edit().putLong("lastSync", v).apply()

    // ---- data received from the PC ------------------------------------------
    private var cache: JSONObject? = null

    private fun sync(): JSONObject {
        cache?.let { return it }
        val parsed = try { JSONObject(syncFile.readText()) } catch (e: Exception) { JSONObject() }
        cache = parsed
        return parsed
    }

    fun saveSync(json: String) {
        JSONObject(json) // reject anything that is not valid JSON before storing it
        syncFile.writeText(json)
        cache = null
        lastSync = System.currentTimeMillis()
    }

    val profiles: List<Profile>
        get() {
            val arr = sync().optJSONArray("profiles") ?: return emptyList()
            return (0 until arr.length()).mapNotNull {
                try { Profile.fromJson(arr.getJSONObject(it)) } catch (e: Exception) { null }
            }.sortedBy { it.name.lowercase() }
        }

    /** Where to reach the PC over Wi-Fi: "host:port" candidates and the access token. */
    val servers: List<String>
        get() {
            val s = sync().optJSONObject("server") ?: return emptyList()
            val addrs = s.optJSONArray("addresses") ?: return emptyList()
            return (0 until addrs.length()).map { "${addrs.getString(it)}:${s.optInt("port", 8377)}" }
        }
    val token: String get() = sync().optJSONObject("server")?.optString("token") ?: ""

    // ---- which profile an output uses -----------------------------------------
    private fun localChoices(): JSONObject =
        try { JSONObject(prefs.getString("choices", "{}")!!) } catch (e: Exception) { JSONObject() }

    /** Remember the user's choice for an output; an empty id means "no EQ". */
    fun choose(output: Output, profileId: String) {
        prefs.edit().putString("choices", localChoices().put(output.key, profileId).toString()).apply()
    }

    /**
     * Profile for an output: the choice made on this phone, else for Bluetooth
     * devices the profile the PC uses for the same headphones (matched by
     * address, or by name when Android hides the address).
     */
    fun profileFor(output: Output): Profile? {
        val all = profiles
        val local = localChoices()
        if (local.has(output.key)) return all.firstOrNull { it.id == local.getString(output.key) }
        if (output.kind != Output.BLUETOOTH) return null
        val pc = sync().optJSONObject("assignments") ?: return null
        val labels = sync().optJSONObject("labels") ?: JSONObject()
        val key = pc.keys().asSequence().firstOrNull { it.equals(output.key, ignoreCase = true) }
            ?: pc.keys().asSequence().firstOrNull { labels.optString(it).equals(output.label, ignoreCase = true) }
            ?: return null
        return all.firstOrNull { it.id == pc.optString(key) }
    }
}
