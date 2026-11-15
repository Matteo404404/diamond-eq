package dev.diamondeq

import java.net.HttpURLConnection
import java.net.URL

/**
 * Fetches profiles from the Diamond EQ service on the PC.
 *
 * Over USB the PC forwards the phone's localhost:8377 to itself (adb reverse),
 * so the first attempt needs no setup. That sync also delivers the PC's network
 * addresses and an access token, which later syncs use over Wi-Fi.
 */
object SyncClient {
    private const val PORT = 8377

    /** Returns a short result message; throws nothing. Call off the main thread. */
    fun run(store: Store): String {
        val candidates = listOf("127.0.0.1:$PORT" to "USB") + store.servers.map { it to "Wi-Fi" }
        val token = store.token
        for ((host, via) in candidates) {
            try {
                val conn = URL("http://$host/api/phone/sync?token=$token").openConnection() as HttpURLConnection
                conn.connectTimeout = 1500
                conn.readTimeout = 8000
                try {
                    if (conn.responseCode != 200) continue
                    val body = conn.inputStream.bufferedReader().readText()
                    store.saveSync(body)
                    return "Synced ${store.profiles.size} profiles over $via"
                } finally {
                    conn.disconnect()
                }
            } catch (e: Exception) {
                // try the next address
            }
        }
        return "PC not reachable. Plug the phone into the PC (USB debugging on), or join the same Wi-Fi."
    }
}
