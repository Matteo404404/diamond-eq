package dev.diamondeq

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build

/** The device music is currently played through. */
data class Output(val key: String, val label: String, val kind: String) {
    companion object {
        const val BLUETOOTH = "bluetooth"
        const val USB = "usb"
        const val WIRED = "wired"
        const val SPEAKER = "speaker"

        private val BT_TYPES = setOf(
            AudioDeviceInfo.TYPE_BLUETOOTH_A2DP, AudioDeviceInfo.TYPE_BLE_HEADSET,
            AudioDeviceInfo.TYPE_BLE_SPEAKER, AudioDeviceInfo.TYPE_HEARING_AID,
        )
        private val USB_TYPES = setOf(
            AudioDeviceInfo.TYPE_USB_HEADSET, AudioDeviceInfo.TYPE_USB_DEVICE,
            AudioDeviceInfo.TYPE_USB_ACCESSORY, AudioDeviceInfo.TYPE_DOCK,
        )
        private val WIRED_TYPES = setOf(
            AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioDeviceInfo.TYPE_WIRED_HEADPHONES,
            AudioDeviceInfo.TYPE_LINE_ANALOG, AudioDeviceInfo.TYPE_AUX_LINE,
        )

        private fun of(d: AudioDeviceInfo): Output {
            val name = d.productName?.toString()?.trim().orEmpty()
            return when (d.type) {
                in BT_TYPES -> {
                    // Android anonymises the address for apps without the Bluetooth permission.
                    val addr = d.address.orEmpty().uppercase()
                    val real = Regex("([0-9A-F]{2}:){5}[0-9A-F]{2}").matches(addr) && addr != "00:00:00:00:00:00"
                    Output(if (real) "bt:$addr" else "btname:$name", name.ifEmpty { "Bluetooth" }, BLUETOOTH)
                }
                in USB_TYPES -> Output("usb:$name", name.ifEmpty { "USB audio" }, USB)
                in WIRED_TYPES -> Output("wired", "Wired headphones", WIRED)
                else -> Output("speaker", "Phone speaker", SPEAKER)
            }
        }

        fun current(context: Context): Output {
            val am = context.getSystemService(AudioManager::class.java)
            if (Build.VERSION.SDK_INT >= 33) {
                val media = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build()
                am.getAudioDevicesForAttributes(media).firstOrNull()?.let { return of(it) }
            }
            // Older Android does not say where media is routed; headphones win over the speaker.
            val outs = am.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
            val pick = outs.firstOrNull { it.type in USB_TYPES } ?: outs.firstOrNull { it.type in WIRED_TYPES }
                ?: outs.firstOrNull { it.type in BT_TYPES }
            return pick?.let { of(it) } ?: Output("speaker", "Phone speaker", SPEAKER)
        }
    }
}
