package dev.diamondeq

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Brings the equalizer back after a reboot if it was switched on. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) EqService.sync(context)
    }
}
